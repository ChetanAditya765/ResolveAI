"""Policy evidence retrieval; PostgreSQL ranks vectors, SQLite supports offline tests."""

import hashlib
import math
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.rag.chunking import CHUNKER_VERSION, chunk_document
from app.rag.contracts import (
    EmbeddingProvider,
    PolicyEvidence,
    PolicySearch,
    PolicySearchResult,
    RagError,
)
from app.rag.embeddings import validate_embeddings

DIMENSIONS = 1536


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _stale_index() -> RagError:
    return RagError(
        "policy_index_stale",
        "Policy sources and their index do not match. Reindex policies before retrying.",
    )


def _validate_document(document: KnowledgeDocument, chunks: list[KnowledgeChunk]) -> None:
    lines = document.content.splitlines()
    header: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("## "):
            break
        if ": " in line:
            key, value = line.split(": ", 1)
            header[key.strip().lower()] = value.strip()
    if (
        not lines
        or lines[0] != f"# {document.title}"
        or header.get("version") != document.version
        or _hash(document.content) != document.content_hash
    ):
        raise _stale_index()

    expected = chunk_document(document.content)
    if len(expected) != len(chunks) or not expected:
        raise _stale_index()
    for source, stored in zip(expected, chunks, strict=True):
        metadata = stored.metadata_
        if (
            not isinstance(metadata, dict)
            or stored.chunk_index != source.index
            or stored.content != source.content
            or metadata.get("section") != source.section
            or metadata.get("version") != document.version
            or metadata.get("document_hash") != document.content_hash
            or metadata.get("chunk_hash") != _hash(stored.content)
            or metadata.get("chunker_version") != CHUNKER_VERSION
        ):
            raise _stale_index()


def _validated_corpus(
    session: Session, provider: EmbeddingProvider
) -> tuple[dict[UUID, KnowledgeDocument], list[KnowledgeChunk]]:
    # The v0.1 corpus is small. Validate every document so a partially refreshed
    # index cannot silently omit a more restrictive policy. Shared row locks keep
    # source text stable until the surrounding tool transaction commits.
    documents = list(
        session.scalars(
            select(KnowledgeDocument)
            .order_by(KnowledgeDocument.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    )
    chunks = list(
        session.scalars(
            select(KnowledgeChunk)
            .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    )
    if not documents or not chunks:
        raise RagError("policy_index_unavailable", "The policy index is not available.")
    by_document: dict[UUID, list[KnowledgeChunk]] = {document.id: [] for document in documents}
    for chunk in chunks:
        if (
            chunk.embedding_provider != provider.provider_name
            or chunk.embedding_model != provider.model_name
            or chunk.embedding is None
        ):
            raise RagError(
                "policy_index_unavailable",
                "The complete policy index must use the configured embedding provider and model.",
            )
        by_document[chunk.document_id].append(chunk)
    for document in documents:
        _validate_document(document, by_document[document.id])
    validate_embeddings(
        [list(chunk.embedding) for chunk in chunks], count=len(chunks), dimensions=DIMENSIONS
    )
    return {document.id: document for document in documents}, chunks


def _query_vector(provider: EmbeddingProvider, query: str) -> list[float]:
    if provider.dimensions != DIMENSIONS:
        raise RagError(
            "embedding_configuration_invalid", "Policy embeddings require 1536 dimensions."
        )
    try:
        vectors = provider.embed([query])
        return validate_embeddings(vectors, count=1, dimensions=DIMENSIONS)[0]
    except RagError:
        raise
    except Exception:
        raise RagError(
            "policy_embedding_failed", "The policy query could not be embedded. Retry later."
        ) from None


def _cosine(left: list[float], right: list[float]) -> float:
    # Scaling before normalization also handles large finite input coordinates.
    left_max = max(abs(value) for value in left)
    right_max = max(abs(value) for value in right)
    left_scaled = [value / left_max for value in left]
    right_scaled = [value / right_max for value in right]
    dot = math.fsum(a * b for a, b in zip(left_scaled, right_scaled, strict=True))
    norm = math.hypot(*left_scaled) * math.hypot(*right_scaled)
    return max(-1.0, min(1.0, dot / norm))


def _evidence(document: KnowledgeDocument, chunk: KnowledgeChunk, score: float) -> PolicyEvidence:
    return PolicyEvidence(
        document_id=str(document.id),
        chunk_id=str(chunk.id),
        slug=document.slug,
        title=document.title,
        version=document.version,
        section=chunk.metadata_["section"],
        excerpt=chunk.content,
        score=max(-1.0, min(1.0, float(score))),
        content_hash=document.content_hash,
    )


def search_policies(
    session: Session,
    args: PolicySearch,
    provider: EmbeddingProvider,
    *,
    min_score: float = 0.1,
) -> PolicySearchResult:
    if not math.isfinite(min_score) or not -1 <= min_score <= 1:
        raise RagError(
            "retrieval_configuration_invalid", "The retrieval score threshold is invalid."
        )
    documents, chunks = _validated_corpus(session, provider)
    vector = _query_vector(provider, args.query)
    if session.get_bind().dialect.name == "postgresql":
        distance = KnowledgeChunk.embedding.cosine_distance(vector)
        query = (
            select(KnowledgeChunk, KnowledgeDocument, (1 - distance).label("score"))
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(
                KnowledgeChunk.id.in_([chunk.id for chunk in chunks]),
                KnowledgeChunk.embedding_provider == provider.provider_name,
                KnowledgeChunk.embedding_model == provider.model_name,
                KnowledgeChunk.embedding.is_not(None),
                distance <= 1 - min_score,
            )
            .order_by(distance, KnowledgeDocument.slug, KnowledgeChunk.chunk_index)
            .limit(args.top_k)
        )
        if args.document_slugs:
            query = query.where(KnowledgeDocument.slug.in_(args.document_slugs))
        evidence = [_evidence(doc, chunk, score) for chunk, doc, score in session.execute(query)]
    elif session.get_bind().dialect.name == "sqlite":
        # This exact cosine implementation is only the offline test/demo adapter.
        # PostgreSQL always performs vector ranking with pgvector above.
        ranked = []
        for chunk in chunks:
            document = documents[chunk.document_id]
            if args.document_slugs and document.slug not in args.document_slugs:
                continue
            score = _cosine(vector, list(chunk.embedding))
            if score >= min_score:
                ranked.append((score, document.slug, chunk.chunk_index, document, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        evidence = [
            _evidence(doc, chunk, score) for score, _, _, doc, chunk in ranked[: args.top_k]
        ]
    else:
        raise RagError("retrieval_database_unsupported", "Policy retrieval requires PostgreSQL.")
    return PolicySearchResult(
        evidence=evidence,
        embedding_provider=provider.provider_name,
        embedding_model=provider.model_name,
        dimensions=DIMENSIONS,
    )
