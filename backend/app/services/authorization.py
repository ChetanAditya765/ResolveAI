"""Revalidate durable workflow evidence at the permission mutation boundary."""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.state import AgentState, DecisionSummary
from app.models import (
    AgentRun,
    AgentRunStatus,
    Employee,
    KnowledgeChunk,
    KnowledgeDocument,
    Permission,
    Repository,
    RepositoryPermission,
    Ticket,
    TicketStatus,
)
from app.services.policy import decide_access
from app.tools.access_schemas import AccessRequest
from app.tools.domain import ToolError
from app.tools.schemas import EmployeeInfo, RepositoryInfo


@dataclass(frozen=True)
class AccessContext:
    run: AgentRun
    ticket: Ticket
    state: AgentState
    employee: Employee
    repository: Repository
    current_permission: Permission
    decision: DecisionSummary


def _validate_evidence(session: Session, state: AgentState) -> None:
    if not state.retrieved_policies:
        raise ToolError("policy_evidence_missing", "Current policy evidence is required.")
    try:
        document_ids = {UUID(item.document_id) for item in state.retrieved_policies}
        chunk_ids = {UUID(item.chunk_id) for item in state.retrieved_policies}
    except ValueError:
        raise ToolError(
            "policy_evidence_invalid", "Policy evidence identifiers are invalid."
        ) from None
    documents = {
        document.id: document
        for document in session.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.id.in_(document_ids))
            .order_by(KnowledgeDocument.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    }
    chunks = {
        chunk.id: chunk
        for chunk in session.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.id.in_(chunk_ids))
            .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    }
    for item in state.retrieved_policies:
        document = documents.get(UUID(item.document_id))
        chunk = chunks.get(UUID(item.chunk_id))
        if (
            document is None
            or chunk is None
            or chunk.document_id != document.id
            or document.slug != item.slug
            or document.title != item.title
            or document.version != item.version
            or document.content_hash != item.content_hash
            or hashlib.sha256(document.content.encode("utf-8")).hexdigest() != item.content_hash
            or chunk.content != item.excerpt
            or not isinstance(chunk.metadata_, dict)
            or chunk.metadata_.get("section") != item.section
            or chunk.metadata_.get("version") != item.version
            or chunk.metadata_.get("document_hash") != item.content_hash
            or chunk.metadata_.get("chunk_hash")
            != hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        ):
            raise ToolError(
                "policy_evidence_stale", "Policy evidence changed; retrieve and review it again."
            )
    if state.decision is None or not set(state.decision.policy_chunk_ids) <= {
        item.chunk_id for item in state.retrieved_policies
    }:
        raise ToolError("policy_evidence_invalid", "The decision has no matching policy evidence.")


def load_context(
    session: Session, run_id: UUID, args: AccessRequest | None = None
) -> AccessContext:
    run = session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None or run.status != AgentRunStatus.RUNNING:
        raise ToolError("run_not_active", "An active agent run is required.")
    ticket = session.scalar(
        select(Ticket)
        .where(Ticket.id == run.ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ticket is None or ticket.status != TicketStatus.PROCESSING:
        raise ToolError("ticket_not_processing", "The run ticket is not processing.")
    try:
        state = AgentState.model_validate(run.state)
    except ValidationError:
        raise ToolError("invalid_run_state", "The saved run state is invalid.") from None
    if (
        state.run_id != run.id
        or state.ticket_id != ticket.id
        or state.graph_thread_id != run.graph_thread_id
        or state.request_text != ticket.request_text
        or state.employee_id != ticket.employee_id
        or state.employee is None
        or state.employee.id != ticket.employee_id
        or state.repository is None
        or state.repository_id != state.repository.id
        or state.intent != "repository_access"
        or state.decision is None
        or state.decision.disposition not in {"grant", "no_change", "approval_required"}
    ):
        raise ToolError("invalid_run_scope", "Saved access scope does not match the active ticket.")
    if state.requested_permission not in {Permission.READ, Permission.WRITE}:
        raise ToolError("permission_not_allowed", "Only read and write requests can be executed.")
    if args is not None and (
        args.employee_id != state.employee_id
        or args.repository_id != state.repository_id
        or args.permission != state.requested_permission
    ):
        raise ToolError("scope_mismatch", "The action must match the saved access request.")
    # Parent-row locks also serialize grants when the permission row does not exist.
    employee = session.scalar(
        select(Employee)
        .where(Employee.id == ticket.employee_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    repository = session.scalar(
        select(Repository)
        .where(Repository.id == state.repository_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if employee is None or not employee.is_active:
        raise ToolError("employee_inactive", "The employee must exist and be active.")
    if repository is None or repository.name != state.repository.name:
        raise ToolError("repository_changed", "The requested repository is unavailable or changed.")
    record = session.scalar(
        select(RepositoryPermission)
        .where(
            RepositoryPermission.employee_id == employee.id,
            RepositoryPermission.repository_id == repository.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current = record.permission if record else Permission.NONE
    _validate_evidence(session, state)
    decision = decide_access(
        EmployeeInfo.model_validate(employee),
        RepositoryInfo.model_validate(repository),
        state.requested_permission,
        current,
        state.retrieved_policies,
    )
    if decision.disposition not in {"grant", "no_change", "approval_required"}:
        raise ToolError("policy_denied", decision.summary)
    return AccessContext(run, ticket, state, employee, repository, current, decision)
