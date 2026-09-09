from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import JSON_VALUE, Base, Timestamps, UUIDPrimaryKey


class KnowledgeDocument(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "knowledge_documents"

    slug: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_VALUE, default=dict, nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document",
        order_by="KnowledgeChunk.chunk_index",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class KnowledgeChunk(UUIDPrimaryKey, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_document_index"),
        CheckConstraint("chunk_index >= 0", name="nonnegative_chunk_index"),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536).with_variant(JSON(), "sqlite")
    )
    embedding_provider: Mapped[str | None] = mapped_column(String(60))
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_VALUE, default=dict, nullable=False
    )

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")
