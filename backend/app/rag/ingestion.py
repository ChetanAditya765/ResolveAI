import hashlib

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.rag.chunking import CHUNKER_VERSION, Chunk, chunk_document
from app.rag.contracts import EmbeddingProvider, RagError
from app.rag.embeddings import DIMENSIONS, validate_embeddings


def _metadata(document: KnowledgeDocument, chunk: Chunk) -> dict[str, str]:
    return {
        "document_hash": document.content_hash,
        "version": document.version,
        "section": chunk.section,
        "chunk_hash": hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
        "chunker_version": CHUNKER_VERSION,
    }


def _is_current(
    document: KnowledgeDocument,
    expected: list[Chunk],
    existing: list[KnowledgeChunk],
    provider: EmbeddingProvider,
) -> bool:
    if len(expected) != len(existing):
        return False
    for chunk, record in zip(expected, existing, strict=True):
        if (
            record.chunk_index != chunk.index
            or record.content != chunk.content
            or record.embedding_provider != provider.provider_name
            or record.embedding_model != provider.model_name
            or record.metadata_ != _metadata(document, chunk)
        ):
            return False
        try:
            validate_embeddings([record.embedding], count=1, dimensions=provider.dimensions)
        except RagError:
            return False
    return True


def ingest_policies(
    session: Session, provider: EmbeddingProvider, *, force: bool = False
) -> dict[str, int]:
    """Replace outdated chunks atomically without committing the caller's transaction.

    Use a dedicated clean session and commit after success. A savepoint restores the
    previous index on failure; all documents are locked until the caller commits.
    """
    if session.new or session.dirty or session.deleted:
        raise ValueError("Ingestion requires a session without pending ORM changes.")
    if provider.dimensions != DIMENSIONS:
        raise RagError("embedding_dimension_mismatch", "The index requires 1536 dimensions.")
    if session.get_bind().dialect.name == "sqlite":
        connection = session.connection()
        # Python 3.12 sqlite3 legacy mode does not begin a transaction for SAVEPOINT.
        # Ensure releasing this savepoint cannot commit the caller's outer unit of work.
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    with session.begin_nested():
        documents = list(
            session.scalars(
                select(KnowledgeDocument).order_by(KnowledgeDocument.id).with_for_update()
            )
        )
        if not documents:
            raise RagError("policies_not_seeded", "Seed policy documents before ingestion.")
        changed: list[tuple[KnowledgeDocument, list[Chunk]]] = []
        for document in documents:
            actual_hash = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
            if actual_hash != document.content_hash:
                raise RagError(
                    "policy_hash_mismatch", "A policy source does not match its recorded hash."
                )
            expected = chunk_document(document.content)
            existing = list(
                session.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id == document.id)
                    .order_by(KnowledgeChunk.chunk_index)
                )
            )
            if force or not _is_current(document, expected, existing, provider):
                changed.append((document, expected))
        texts = [chunk.content for _, chunks in changed for chunk in chunks]
        vectors = validate_embeddings(
            provider.embed(texts) if texts else [],
            count=len(texts),
            dimensions=provider.dimensions,
        )
        vector_index = 0
        for document, chunks in changed:
            session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
            for chunk in chunks:
                session.add(
                    KnowledgeChunk(
                        document_id=document.id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        embedding=vectors[vector_index],
                        embedding_provider=provider.provider_name,
                        embedding_model=provider.model_name,
                        metadata_=_metadata(document, chunk),
                    )
                )
                vector_index += 1
        session.flush()
    return {
        "documents_indexed": len(changed),
        "documents_skipped": len(documents) - len(changed),
        "chunks_created": len(texts),
    }
