from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON_VALUE, Base, UTCDateTime, UUIDPrimaryKey, utc_now
from app.models.enums import AgentRunStatus, AgentStepStatus, ToolExecutionStatus, enum_column


class AgentRun(UUIDPrimaryKey, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index(
            "uq_agent_runs_active_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING', 'WAITING_FOR_APPROVAL')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING', 'WAITING_FOR_APPROVAL')"),
        ),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_column(AgentRunStatus, "agent_run_status"),
        default=AgentRunStatus.PENDING,
        index=True,
        nullable=False,
    )
    state: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    graph_thread_id: Mapped[str] = mapped_column(
        String(80), default=lambda: str(uuid4()), unique=True, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    recovery_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    provider_name: Mapped[str] = mapped_column(
        String(40), default="demo", server_default="demo", nullable=False
    )
    model_name: Mapped[str] = mapped_column(
        String(120),
        default="deterministic-access-v1",
        server_default="deterministic-access-v1",
        nullable=False,
    )


class AgentStep(UUIDPrimaryKey, Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_step_run_sequence"),
        CheckConstraint("sequence >= 0", name="nonnegative_sequence"),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="nonnegative_latency"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[AgentStepStatus] = mapped_column(
        enum_column(AgentStepStatus, "agent_step_status"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class ToolExecution(UUIDPrimaryKey, Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="nonnegative_latency"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="RESTRICT"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON_VALUE)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        enum_column(ToolExecutionStatus, "tool_execution_status"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    latency_ms: Mapped[int | None] = mapped_column(Integer)
