from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON_VALUE, Base, CreatedAt, UTCDateTime, UUIDPrimaryKey


class EvaluationBatch(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "evaluation_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')", name="batch_status"
        ),
        CheckConstraint(
            "completed_count >= 0 AND completed_count <= total_count", name="batch_progress"
        ),
        Index("uq_evaluation_active_batch", "active_slot", unique=True),
    )
    requested_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    submission_key: Mapped[UUID] = mapped_column(Uuid, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    # All active submissions share slot 1; completed batches release it to NULL.
    active_slot: Mapped[int | None] = mapped_column(Integer, default=1)
    scenario_ids: Mapped[list[str]] = mapped_column(JSON_VALUE, nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(
        String(32), default="deterministic-v2", server_default="deterministic-v2", nullable=False
    )
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)


class EvaluationResult(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="nonnegative_latency"),
        CheckConstraint("source IN ('live', 'scenario')", name="evaluation_source"),
        UniqueConstraint("run_id", "evaluator_version", name="uq_evaluation_run_version"),
        UniqueConstraint(
            "batch_id", "scenario_id", "evaluator_version", name="uq_evaluation_batch_scenario"
        ),
    )

    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), index=True
    )
    scenario_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), default="live", server_default="live", nullable=False
    )
    batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evaluation_batches.id", ondelete="RESTRICT"), index=True
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON_VALUE, default=dict, server_default=text("'{}'"), nullable=False
    )
    evaluator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    assertions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, default=list, nullable=False
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
