from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.state import AgentState
from app.models import AgentRun, ApprovalRequest, Employee, User, UserRole
from app.services.authorization import load_context
from app.tools.access_schemas import AccessRequest, ApprovalInfo, ApprovalLookup
from app.tools.domain import ToolError


def create_approval_request(session: Session, args: AccessRequest, *, run_id: UUID) -> ApprovalInfo:
    context = load_context(session, run_id, args)
    if context.decision.disposition != "approval_required":
        raise ToolError("approval_not_required", "Current policy does not require this approval.")
    manager = (
        session.get(Employee, context.employee.manager_id) if context.employee.manager_id else None
    )
    if manager is None or not manager.is_active or manager.id == context.employee.id:
        raise ToolError(
            "manager_unavailable", "An active recorded manager is required for approval."
        )
    approver = session.scalar(select(User).where(User.employee_id == manager.id))
    if approver is None or approver.role not in (UserRole.MANAGER, UserRole.ADMIN):
        raise ToolError("manager_unavailable", "The recorded manager has no reviewer account.")
    approval = session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id))
    evidence = [item.model_dump(mode="json") for item in context.state.retrieved_policies]
    if approval is None:
        approval = ApprovalRequest(
            run_id=run_id,
            ticket_id=context.ticket.id,
            employee_id=args.employee_id,
            repository_id=args.repository_id,
            permission=args.permission,
            approver_id=approver.id,
            policy_evidence=evidence,
            recommendation=context.decision.summary,
        )
        session.add(approval)
        session.flush()
    elif (
        approval.ticket_id != context.ticket.id
        or approval.employee_id != args.employee_id
        or approval.repository_id != args.repository_id
        or approval.permission != args.permission
        or approval.policy_evidence != evidence
        or approval.approver_id != approver.id
    ):
        raise ToolError("approval_scope_mismatch", "An existing approval has a different scope.")
    return ApprovalInfo.model_validate(approval)


def get_approval_status(session: Session, args: ApprovalLookup, *, run_id: UUID) -> ApprovalInfo:
    run = session.get(AgentRun, run_id)
    approval = session.get(ApprovalRequest, args.approval_id)
    if run is None or approval is None:
        raise ToolError("approval_missing", "The recorded approval could not be found.")
    state = AgentState.model_validate(run.state)
    if (
        approval.run_id != run_id
        or approval.ticket_id != run.ticket_id
        or approval.employee_id != state.employee_id
        or approval.repository_id != state.repository_id
        or approval.permission != state.requested_permission
        or state.approval_id != approval.id
    ):
        raise ToolError(
            "approval_scope_mismatch", "The approval does not match the active request."
        )
    return ApprovalInfo.model_validate(approval)
