"""Guarded mock access management; callers persist the mutation and audit atomically."""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import (
    AgentStep,
    AgentStepStatus,
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    ConversationRole,
    Employee,
    Permission,
    RepositoryPermission,
    TicketStatus,
    ToolExecution,
    ToolExecutionStatus,
    User,
    UserRole,
)
from app.services.authorization import AccessContext, load_context
from app.tools.access_schemas import CloseRequest, CloseResult, GrantRequest, GrantResult
from app.tools.domain import ToolError
from app.tools.schemas import PermissionInfo, PermissionLookup, ToolResult

_RANK = {Permission.NONE: 0, Permission.READ: 1, Permission.WRITE: 2, Permission.ADMIN: 3}


def _validate_approval(session: Session, context: AccessContext, approval_id: UUID | None) -> None:
    saved_id = context.state.approval_id
    required = (
        context.decision.disposition == "approval_required"
        or context.state.decision.disposition == "approval_required"
        or saved_id is not None
        or approval_id is not None
    )
    if not required:
        return
    if saved_id is None or approval_id != saved_id:
        raise ToolError(
            "approval_missing", "The exact saved approval is required before execution."
        )
    approval = session.scalar(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == saved_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if approval is None or (
        approval.run_id != context.run.id
        or approval.ticket_id != context.ticket.id
        or approval.employee_id != context.employee.id
        or approval.repository_id != context.repository.id
        or approval.permission != context.state.requested_permission
        or approval.policy_evidence
        != [item.model_dump(mode="json") for item in context.state.retrieved_policies]
    ):
        raise ToolError(
            "approval_scope_mismatch", "Approval does not cover this request and evidence."
        )
    if approval.status != ApprovalStatus.APPROVED:
        raise ToolError("approval_not_granted", "The access request has not been approved.")
    if approval.decided_at is None or approval.decided_by_id is None:
        raise ToolError(
            "approval_audit_invalid", "The approval decision has no valid reviewer audit."
        )
    reviewer = session.scalar(
        select(User)
        .where(User.id == approval.decided_by_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    reviewer_employee = (
        session.scalar(
            select(Employee)
            .where(Employee.id == reviewer.employee_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if reviewer is not None and reviewer.employee_id is not None
        else None
    )
    if (
        reviewer is None
        or reviewer_employee is None
        or not reviewer_employee.is_active
        or reviewer_employee.id == context.employee.id
    ):
        raise ToolError(
            "approval_reviewer_invalid", "The approval reviewer is unavailable or invalid."
        )
    if reviewer.role == UserRole.ADMIN:
        return
    if (
        reviewer.role != UserRole.MANAGER
        or reviewer.id != approval.approver_id
        or reviewer.employee_id != context.employee.manager_id
    ):
        raise ToolError(
            "approval_reviewer_changed", "The designated manager must still be authorized."
        )


def grant_repository_permission(
    session: Session, args: GrantRequest, *, run_id: UUID
) -> GrantResult:
    context = load_context(session, run_id, args)
    _validate_approval(session, context, args.approval_id)
    previous = context.current_permission
    if _RANK[previous] >= _RANK[args.permission]:
        return GrantResult(
            employee_id=args.employee_id,
            repository_id=args.repository_id,
            previous_permission=previous,
            observed_permission=previous,
            changed=False,
        )
    record = session.scalar(
        select(RepositoryPermission).where(
            RepositoryPermission.employee_id == args.employee_id,
            RepositoryPermission.repository_id == args.repository_id,
        )
    )
    if record is None:
        record = RepositoryPermission(
            employee_id=args.employee_id,
            repository_id=args.repository_id,
            permission=args.permission,
        )
        session.add(record)
    else:
        record.permission = args.permission
        record.updated_at = utc_now()
    session.flush()
    return GrantResult(
        employee_id=args.employee_id,
        repository_id=args.repository_id,
        previous_permission=previous,
        observed_permission=record.permission,
        changed=True,
    )


def _validate_verification(session: Session, context: AccessContext) -> None:
    verification = context.state.verification_result
    if verification is None or not verification.sufficient:
        raise ToolError(
            "verification_required", "A successful permission verification is required."
        )
    try:
        execution_id = UUID(verification.tool_execution_id)
    except ValueError:
        raise ToolError("verification_invalid", "The verification trace is invalid.") from None
    execution = session.get(ToolExecution, execution_id)
    step = session.get(AgentStep, execution.step_id) if execution and execution.step_id else None
    if (
        execution is None
        or execution.run_id != context.run.id
        or execution.tool_name != "get_repository_permission"
        or execution.status != ToolExecutionStatus.SUCCEEDED
        or execution.completed_at is None
        or step is None
        or step.run_id != context.run.id
        or step.node != "VERIFY_ACTION"
        or step.status != AgentStepStatus.COMPLETED
    ):
        raise ToolError("verification_invalid", "A completed verification tool trace is required.")
    try:
        args = PermissionLookup.model_validate(execution.arguments)
        result = ToolResult.model_validate((execution.result or {}).get("tool_result"))
        observed = PermissionInfo.model_validate(result.output)
    except ValidationError:
        raise ToolError("verification_invalid", "The verification output is invalid.") from None
    if (
        args.employee_id != context.employee.id
        or args.repository_id != context.repository.id
        or result.execution_id != execution.id
        or result.tool_name != execution.tool_name
        or not result.succeeded
        or result.error_code is not None
        or observed.employee_id != context.employee.id
        or observed.repository_id != context.repository.id
        or observed.permission != verification.observed_permission
        or observed.permission != context.current_permission
        or _RANK[observed.permission] < _RANK[context.state.requested_permission]
    ):
        raise ToolError(
            "verification_mismatch", "Verified permission does not match current access."
        )


def close_ticket(session: Session, args: CloseRequest, *, run_id: UUID) -> CloseResult:
    context = load_context(session, run_id)
    if args.ticket_id != context.ticket.id:
        raise ToolError("scope_mismatch", "Only the active run ticket may be closed.")
    _validate_approval(session, context, context.state.approval_id)
    _validate_verification(session, context)
    cited_ids = set(context.state.decision.policy_chunk_ids) | set(
        context.decision.policy_chunk_ids
    )
    citations = "; ".join(
        dict.fromkeys(
            f"{item.title} {item.section}"
            for item in context.state.retrieved_policies
            if item.chunk_id in cited_ids
        )
    )
    final_response = (
        f"Verified {context.current_permission.value} access to the {context.repository.name} "
        f"repository for {context.employee.name}. Your {context.state.requested_permission.value} "
        f"access request is satisfied. Policy: {citations}."
    )
    now = utc_now()
    context.ticket.status = TicketStatus.RESOLVED
    context.ticket.resolved_at = now
    context.ticket.updated_at = now
    context.ticket.final_response = final_response
    session.add(
        ConversationMessage(
            ticket_id=context.ticket.id,
            role=ConversationRole.ASSISTANT,
            content=final_response,
        )
    )
    session.flush()
    return CloseResult(
        ticket_id=context.ticket.id,
        final_response=final_response,
        observed_permission=context.current_permission,
    )
