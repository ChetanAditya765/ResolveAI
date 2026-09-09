import hashlib
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.rag.chunking import CHUNKER_VERSION, chunk_document
from app.rag.contracts import PolicySearch, RagError
from app.rag.retrieval import search_policies


def vector(first: float = 1.0, second: float = 0.0) -> list[float]:
    return [first, second, *([0.0] * 1534)]


class QueryEmbedding:
    provider_name = "retrieval-test"
    model_name = "vectors-v1"
    dimensions = 1536

    def __init__(self, output: list[list[float]] | None = None) -> None:
        self.output = output if output is not None else [vector()]
        self.queries: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.queries.extend(texts)
        return self.output


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.fixture
def indexed_session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        # Insertion order deliberately differs from the stable retrieval order.
        for slug, sections, embedding in (
            ("zulu", ["Write access requires approval."], vector()),
            ("alpha", ["Read access can be granted.", "Admin access must be escalated."], vector()),
            ("other", ["Offboarding removes access."], vector(0, 1)),
        ):
            content = f"# {slug.title()} Policy\n\nVersion: 1.0\n\n" + "\n\n".join(
                f"## §{index + 1}. Scope\n\n{body}" for index, body in enumerate(sections)
            )
            document = KnowledgeDocument(
                slug=slug,
                title=f"{slug.title()} Policy",
                version="1.0",
                content=content,
                content_hash=digest(content),
            )
            session.add(document)
            session.flush()
            for chunk in chunk_document(content):
                session.add(
                    KnowledgeChunk(
                        document_id=document.id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        embedding=embedding,
                        embedding_provider="retrieval-test",
                        embedding_model="vectors-v1",
                        metadata_={
                            "document_hash": document.content_hash,
                            "version": document.version,
                            "section": chunk.section,
                            "chunk_hash": digest(chunk.content),
                            "chunker_version": CHUNKER_VERSION,
                        },
                    )
                )
        session.commit()
        yield session


def first_chunk(session: Session) -> KnowledgeChunk:
    return session.scalars(
        select(KnowledgeChunk).order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
    ).first()


def test_retrieval_returns_exact_source_and_stable_top_k(indexed_session: Session) -> None:
    provider = QueryEmbedding()
    result = search_policies(indexed_session, PolicySearch(query="write access", top_k=2), provider)
    assert provider.queries == ["write access"]
    assert [(hit.slug, hit.section) for hit in result.evidence] == [
        ("alpha", "§1. Scope"),
        ("alpha", "§2. Scope"),
    ]
    assert result.embedding_provider == "retrieval-test"
    assert result.embedding_model == "vectors-v1"
    assert result.dimensions == 1536
    for hit in result.evidence:
        document = indexed_session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.slug == hit.slug)
        )
        assert hit.document_id == str(document.id)
        assert hit.content_hash == document.content_hash
        assert hit.title == document.title
        assert hit.version == document.version
        assert hit.excerpt in document.content
        assert hit.score == pytest.approx(1.0)
        assert any(str(chunk.id) == hit.chunk_id for chunk in document.chunks)


def test_document_filter_applies_before_top_k(indexed_session: Session) -> None:
    result = search_policies(
        indexed_session,
        PolicySearch(query="write access", top_k=1, document_slugs=["zulu"]),
        QueryEmbedding(),
    )
    assert [hit.slug for hit in result.evidence] == ["zulu"]


@pytest.mark.parametrize("slugs", [["unknown-policy"], ["other"]])
def test_no_relevant_evidence_returns_empty(indexed_session: Session, slugs: list[str]) -> None:
    result = search_policies(
        indexed_session,
        PolicySearch(query="write access", document_slugs=slugs),
        QueryEmbedding(),
    )
    assert result.evidence == []


def test_actual_cosine_similarity_controls_order(indexed_session: Session) -> None:
    result = search_policies(
        indexed_session,
        PolicySearch(query="remove employee access"),
        QueryEmbedding([vector(0, 3)]),
    )
    assert [hit.slug for hit in result.evidence] == ["other"]


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), -1.1, 1.1])
def test_invalid_threshold_fails_closed(indexed_session: Session, threshold: float) -> None:
    with pytest.raises(RagError, match="threshold"):
        search_policies(
            indexed_session,
            PolicySearch(query="write access"),
            QueryEmbedding(),
            min_score=threshold,
        )


def test_empty_corpus_is_not_successful_retrieval(engine: Engine) -> None:
    with Session(engine) as session, pytest.raises(RagError, match="not available"):
        search_policies(session, PolicySearch(query="write access"), QueryEmbedding())


@pytest.mark.parametrize(
    ("field", "value"),
    [("embedding_provider", "wrong"), ("embedding_model", "wrong"), ("embedding", None)],
)
def test_mixed_or_missing_index_fails_closed(
    indexed_session: Session, field: str, value: object
) -> None:
    setattr(first_chunk(indexed_session), field, value)
    with pytest.raises(RagError, match="complete policy index"):
        search_policies(indexed_session, PolicySearch(query="write access"), QueryEmbedding())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("section", "§999. Invented policy"),
        ("document_hash", "a" * 64),
        ("version", "999"),
        ("chunk_hash", "a" * 64),
        ("chunker_version", "unknown"),
    ],
)
def test_stale_or_tampered_metadata_fails_closed(
    indexed_session: Session, field: str, value: str
) -> None:
    chunk = first_chunk(indexed_session)
    chunk.metadata_ = {**chunk.metadata_, field: value}
    with pytest.raises(RagError, match="do not match"):
        search_policies(indexed_session, PolicySearch(query="write access"), QueryEmbedding())


def test_mutated_excerpt_cannot_be_laundered_with_new_chunk_hash(indexed_session: Session) -> None:
    chunk = first_chunk(indexed_session)
    chunk.content = "Write access requires no approval."
    chunk.metadata_ = {**chunk.metadata_, "chunk_hash": digest(chunk.content)}
    with pytest.raises(RagError, match="do not match"):
        search_policies(indexed_session, PolicySearch(query="write access"), QueryEmbedding())


@pytest.mark.parametrize("field", ["title", "version", "content", "content_hash"])
def test_changed_source_requires_reingestion(indexed_session: Session, field: str) -> None:
    document = indexed_session.scalars(select(KnowledgeDocument)).first()
    setattr(document, field, "changed source")
    with pytest.raises(RagError, match="do not match"):
        search_policies(indexed_session, PolicySearch(query="write access"), QueryEmbedding())


def test_partial_corpus_fails_even_when_missing_document_is_filtered_out(
    indexed_session: Session,
) -> None:
    document = indexed_session.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.slug == "other")
    )
    indexed_session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
    with pytest.raises(RagError, match="do not match"):
        search_policies(
            indexed_session,
            PolicySearch(query="write access", document_slugs=["zulu"]),
            QueryEmbedding(),
        )


def test_missing_section_cannot_silently_weaken_policy(indexed_session: Session) -> None:
    document = indexed_session.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.slug == "alpha")
    )
    indexed_session.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.document_id == document.id, KnowledgeChunk.chunk_index == 1
        )
    )
    with pytest.raises(RagError, match="do not match"):
        search_policies(indexed_session, PolicySearch(query="write access"), QueryEmbedding())


@pytest.mark.parametrize(
    "output",
    [
        [],
        [vector(), vector()],
        [[1.0]],
        [[0.0] * 1536],
        [vector(float("nan"))],
        [vector(float("inf"))],
    ],
)
def test_malformed_query_vectors_fail_closed(
    indexed_session: Session, output: list[list[float]]
) -> None:
    with pytest.raises(RagError):
        search_policies(indexed_session, PolicySearch(query="write access"), QueryEmbedding(output))


def test_invalid_stored_vector_is_not_cited(indexed_session: Session) -> None:
    first_chunk(indexed_session).embedding = [0.0] * 1536
    with pytest.raises(RagError):
        search_policies(indexed_session, PolicySearch(query="write access"), QueryEmbedding())


def test_provider_exceptions_are_sanitized(indexed_session: Session) -> None:
    class BrokenProvider(QueryEmbedding):
        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("secret-key-and-private-provider-response")

    with pytest.raises(RagError) as raised:
        search_policies(indexed_session, PolicySearch(query="write access"), BrokenProvider())
    assert raised.value.code == "policy_embedding_failed"
    assert "secret" not in str(raised.value)


def test_wrong_configured_dimensions_fail_closed(indexed_session: Session) -> None:
    provider = QueryEmbedding()
    provider.dimensions = 768
    with pytest.raises(RagError, match="1536"):
        search_policies(indexed_session, PolicySearch(query="write access"), provider)
