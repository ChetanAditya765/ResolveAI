from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON_VALUE, Base, UTCDateTime, UUIDPrimaryKey, utc_now
from app.models.enums import ApprovalStatus, Permission, enum_column


class ApprovalRequest(UUIDPrimaryKey, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_approval_request_run"),
        CheckConstraint(
            "(status = 'PENDING' AND decided_at IS NULL AND decided_by_id IS NULL) OR "
            "(status <> 'PENDING' AND decided_at IS NOT NULL AND decided_by_id IS NOT NULL)",
            name="decision_audit_consistent",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False
    )
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT"), nullable=False
    )
    permission: Mapped[Permission] = mapped_column(
        enum_column(Permission, "approval_permission_level"), nullable=False
    )
    approver_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_column(ApprovalStatus, "approval_status"),
        default=ApprovalStatus.PENDING,
        index=True,
        nullable=False,
    )
    policy_evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_VALUE, default=list, nullable=False
    )
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decided_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decision_comment: Mapped[str | None] = mapped_column(Text)
