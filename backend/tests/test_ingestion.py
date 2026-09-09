import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.db.seed import seed_database
from app.models import KnowledgeChunk, KnowledgeDocument
from app.rag.chunking import CHUNKER_VERSION, chunk_document
from app.rag.contracts import RagError
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies


def test_chunking_preserves_whole_section_bodies_and_excludes_headers() -> None:
    chunks = chunk_document(
        "# Policy\r\nVersion: 1.0\r\n\r\n## §1. Scope\r\n\r\nBody one.\r\n\r\n"
        "## §2. Approval\r\nBody two.\r\n"
    )
    assert [(chunk.index, chunk.section, chunk.content) for chunk in chunks] == [
        (0, "§1. Scope", "Body one."),
        (1, "§2. Approval", "Body two."),
    ]


def test_chunking_bounds_size_and_never_overlaps_sections() -> None:
    body = " ".join(f"word{index}" for index in range(900))
    chunks = chunk_document(f"## First\n{body}\n\n## Second\nUnique second body.")
    first = [chunk for chunk in chunks if chunk.section == "First"]
    assert len(first) > 1
    assert all(0 < len(chunk.content) <= 1800 for chunk in chunks)
    assert chunks[-1].content == "Unique second body."
    assert all("Unique" not in chunk.content for chunk in first)
    for left, right in zip(first, first[1:], strict=False):
        assert 0 < len(set(left.content.split()) & set(right.content.split())) < 25


@pytest.mark.parametrize("content", ["", "# Title\nVersion: 1.0", "## Empty\n"])
def test_chunking_rejects_documents_without_searchable_sections(content: str) -> None:
    with pytest.raises(RagError) as error:
        chunk_document(content)
    assert error.value.code == "invalid_policy_document"


def test_all_seeded_policy_sections_fit_whole(policy_directory: Path) -> None:
    for path in policy_directory.glob("*.md"):
        source = path.read_text(encoding="utf-8")
        chunks = chunk_document(source)
        assert len({chunk.section for chunk in chunks}) == len(chunks)


def snapshot(session: Session) -> list[tuple]:
    return [
        (
            row.id,
            row.document_id,
            row.chunk_index,
            row.content,
            row.embedding_provider,
            row.embedding_model,
            row.metadata_,
        )
        for row in session.scalars(
            select(KnowledgeChunk).order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
        )
    ]


def test_ingestion_persists_complete_versioned_index_and_is_idempotent(db_session: Session) -> None:
    provider = LocalHashEmbeddingProvider()
    counts = ingest_policies(db_session, provider)
    db_session.commit()
    assert counts["documents_indexed"] == 8
    assert counts["documents_skipped"] == 0
    assert counts["chunks_created"] > 8
    before = snapshot(db_session)
    for chunk in db_session.scalars(select(KnowledgeChunk)):
        document = db_session.get(KnowledgeDocument, chunk.document_id)
        assert chunk.metadata_["document_hash"] == document.content_hash
        assert chunk.metadata_["version"] == document.version
        assert chunk.metadata_["chunk_hash"] == hashlib.sha256(chunk.content.encode()).hexdigest()
        assert chunk.metadata_["chunker_version"] == CHUNKER_VERSION
        assert len(chunk.embedding) == 1536
    provider.embed = Mock(side_effect=AssertionError("Unchanged sources must not be embedded"))
    assert ingest_policies(db_session, provider) == {
        "documents_indexed": 0,
        "documents_skipped": 8,
        "chunks_created": 0,
    }
    assert snapshot(db_session) == before


def test_ingestion_does_not_commit_callers_transaction(db_session: Session) -> None:
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0


@pytest.mark.parametrize("corruption", ["missing", "metadata", "embedding", "content", "model"])
def test_ingestion_repairs_partial_or_stale_document_index(
    db_session: Session, corruption: str
) -> None:
    provider = LocalHashEmbeddingProvider()
    ingest_policies(db_session, provider)
    db_session.commit()
    row = db_session.scalar(select(KnowledgeChunk))
    if corruption == "missing":
        db_session.delete(row)
    elif corruption == "metadata":
        row.metadata_ = {**row.metadata_, "version": "stale"}
    elif corruption == "embedding":
        row.embedding = [0.0] * 1536
    elif corruption == "content":
        row.content = "tampered"
    else:
        row.embedding_model = "old-model"
    db_session.commit()
    counts = ingest_policies(db_session, provider)
    assert counts["documents_indexed"] == 1
    assert counts["documents_skipped"] == 7
    db_session.commit()
    assert ingest_policies(db_session, provider)["documents_indexed"] == 0


def test_model_switch_replaces_entire_corpus_atomically(db_session: Session) -> None:
    provider = LocalHashEmbeddingProvider()
    ingest_policies(db_session, provider)
    db_session.commit()
    old_ids = {row[0] for row in snapshot(db_session)}
    provider.model_name = "replacement-model"
    counts = ingest_policies(db_session, provider)
    db_session.commit()
    assert counts["documents_indexed"] == 8
    rows = list(db_session.scalars(select(KnowledgeChunk)))
    assert {row.embedding_model for row in rows} == {"replacement-model"}
    assert not old_ids.intersection(row.id for row in rows)


@pytest.mark.parametrize("failure", ["provider", "malformed"])
def test_failed_reindex_preserves_every_previous_chunk(db_session: Session, failure: str) -> None:
    provider = LocalHashEmbeddingProvider()
    ingest_policies(db_session, provider)
    db_session.commit()
    before = snapshot(db_session)
    provider.embed = (
        Mock(side_effect=RagError("embedding_timeout", "The embedding request timed out."))
        if failure == "provider"
        else Mock(return_value=[[0.0] * 1536])
    )
    with pytest.raises(RagError):
        ingest_policies(db_session, provider, force=True)
    assert snapshot(db_session) == before
    db_session.commit()
    assert snapshot(db_session) == before


def test_hash_mismatch_fails_before_any_embedding_or_replacement(db_session: Session) -> None:
    provider = LocalHashEmbeddingProvider()
    ingest_policies(db_session, provider)
    db_session.commit()
    before = snapshot(db_session)
    document = db_session.scalar(select(KnowledgeDocument))
    document.content += "\nTampered source."
    db_session.commit()
    provider.embed = Mock(side_effect=AssertionError("Do not send unverified policy sources"))
    with pytest.raises(RagError) as error:
        ingest_policies(db_session, provider)
    assert error.value.code == "policy_hash_mismatch"
    assert snapshot(db_session) == before


def test_database_failure_mid_replacement_rolls_back_the_whole_corpus(
    db_session: Session, engine
) -> None:
    provider = LocalHashEmbeddingProvider()
    ingest_policies(db_session, provider)
    db_session.commit()
    before = snapshot(db_session)
    deletions = 0

    def fail_second_document(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal deletions
        if statement.startswith("DELETE FROM knowledge_chunks"):
            deletions += 1
            if deletions == 2:
                raise RuntimeError("Injected failure after replacing the first document")

    event.listen(engine, "before_cursor_execute", fail_second_document)
    try:
        with pytest.raises(RuntimeError, match="Injected failure"):
            ingest_policies(db_session, provider, force=True)
    finally:
        event.remove(engine, "before_cursor_execute", fail_second_document)
    assert deletions == 2
    assert snapshot(db_session) == before
    db_session.commit()
    assert snapshot(db_session) == before


def test_ingestion_rejects_unflushed_unrelated_changes(db_session: Session) -> None:
    document = db_session.scalar(select(KnowledgeDocument))
    document.title = "Uncommitted edit"
    with pytest.raises(ValueError, match="pending ORM"):
        ingest_policies(db_session, LocalHashEmbeddingProvider())
    db_session.rollback()
    assert document.title != "Uncommitted edit"


def test_seed_and_ingestion_share_caller_transaction(engine, policy_directory: Path) -> None:
    with Session(engine) as session:
        seed_database(session, policy_directory)
        ingest_policies(session, LocalHashEmbeddingProvider())
        session.rollback()
        assert session.scalar(select(func.count()).select_from(KnowledgeChunk)) == 0
