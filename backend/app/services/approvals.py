from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.state import AgentState
from app.core.errors import DomainError
from app.db.base import utc_now
from app.models import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
    Employee,
    Ticket,
    TicketStatus,
    User,
    UserRole,
)


def active_actor(session: Session, actor: User) -> User:
    current = session.scalar(
        select(User).where(User.id == actor.id).execution_options(populate_existing=True)
    )
    employee = session.get(Employee, current.employee_id) if current else None
    if current is None or employee is None or not employee.is_active:
        raise DomainError(403, "reviewer_unavailable", "An active reviewer is required.")
    return current


def may_view(approval: ApprovalRequest, actor: User) -> bool:
    return (
        actor.role == UserRole.ADMIN
        or actor.id == approval.approver_id
        or actor.employee_id == approval.employee_id
    )


def get_approval(session: Session, approval_id: UUID, actor: User) -> ApprovalRequest:
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise DomainError(404, "approval_not_found", "Approval request does not exist.")
    if not may_view(approval, active_actor(session, actor)):
        raise DomainError(403, "approval_forbidden", "This approval is not available to this user.")
    return approval


def _authorize_reviewer(session: Session, approval: ApprovalRequest, actor: User) -> None:
    actor = active_actor(session, actor)
    employee = session.get(Employee, approval.employee_id)
    if actor.employee_id == approval.employee_id:
        raise DomainError(
            403, "self_approval_forbidden", "Employees cannot review their own access requests."
        )
    if actor.role == UserRole.ADMIN:
        return
    if (
        actor.role != UserRole.MANAGER
        or actor.id != approval.approver_id
        or employee is None
        or employee.manager_id != actor.employee_id
    ):
        raise DomainError(
            403, "approval_forbidden", "Only the assigned manager or an administrator may review."
        )


def decide_approval(
    session: Session, approval_id: UUID, actor: User, status: ApprovalStatus, comment: str | None
) -> ApprovalRequest:
    if status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
        raise DomainError(422, "invalid_decision", "Approve or reject the pending request.")
    found = session.get(ApprovalRequest, approval_id)
    if found is None:
        raise DomainError(404, "approval_not_found", "Approval request does not exist.")
    run = session.scalar(select(AgentRun).where(AgentRun.id == found.run_id).with_for_update())
    ticket = session.scalar(select(Ticket).where(Ticket.id == found.ticket_id).with_for_update())
    approval = session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    _authorize_reviewer(session, approval, actor)
    if approval.status != ApprovalStatus.PENDING:
        if approval.status != status:
            raise DomainError(409, "decision_conflict", "A different decision is already recorded.")
        session.commit()
        return approval
    if (
        run is None
        or ticket is None
        or run.status != AgentRunStatus.WAITING_FOR_APPROVAL
        or ticket.status != TicketStatus.WAITING_FOR_APPROVAL
    ):
        raise DomainError(
            409, "approval_not_ready", "The agent has not reached its durable approval pause."
        )
    try:
        state = AgentState.model_validate(run.state)
    except ValidationError:
        raise DomainError(409, "approval_scope_mismatch", "The saved request is invalid.") from None
    if (
        state.approval_id != approval.id
        or state.run_id != run.id
        or state.ticket_id != ticket.id
        or run.ticket_id != ticket.id
        or approval.employee_id != ticket.employee_id
        or state.employee_id != ticket.employee_id
        or state.repository_id != approval.repository_id
        or state.requested_permission != approval.permission
    ):
        raise DomainError(
            409, "approval_scope_mismatch", "The approval no longer matches this request."
        )
    approval.status = status
    approval.decided_at = utc_now()
    approval.decided_by_id = actor.id
    approval.decision_comment = comment
    run.status = AgentRunStatus.PENDING
    run.lease_owner, run.lease_expires_at = None, None
    run.recovery_attempt_count = 0
    run.queued_at = utc_now()
    ticket.status = TicketStatus.PROCESSING
    session.commit()
    session.refresh(approval)
    return approval
