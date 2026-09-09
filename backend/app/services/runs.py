from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agents.state import AgentState, RetrievalConfig, WorkflowNode
from app.core.config import Settings
from app.core.errors import DomainError, LeaseLost
from app.db.base import utc_now
from app.models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    ApprovalRequest,
    ApprovalStatus,
    ConversationMessage,
    ConversationRole,
    Ticket,
    TicketStatus,
)
from app.services.tickets import get_ticket

ACTIVE = (AgentRunStatus.PENDING, AgentRunStatus.RUNNING, AgentRunStatus.WAITING_FOR_APPROVAL)


def submit_run(session: Session, ticket_id: UUID, settings: Settings) -> AgentRun:
    ticket = get_ticket(session, ticket_id, for_update=True)
    active = session.scalar(
        select(AgentRun).where(AgentRun.ticket_id == ticket_id, AgentRun.status.in_(ACTIVE))
    )
    if active:
        return active
    if ticket.status != TicketStatus.OPEN:
        raise DomainError(409, "ticket_not_open", "Only open tickets can start a new run.")
    if "openai" in (settings.llm_provider, settings.embedding_provider) and (
        not settings.openai_api_key or not settings.openai_api_key.get_secret_value().strip()
    ):
        raise DomainError(503, "provider_not_configured", "The OpenAI provider is not configured.")
    run_id = uuid4()
    thread_id = str(run_id)
    from app.rag.embeddings import embedding_identity

    embedding_provider, embedding_model = embedding_identity(settings)
    state = AgentState(
        ticket_id=ticket.id,
        run_id=run_id,
        graph_thread_id=thread_id,
        request_text=ticket.request_text,
        employee_id=ticket.employee_id,
        workflow_version=3,
        retrieval_config=RetrievalConfig(
            provider=embedding_provider,
            model=embedding_model,
            top_k=settings.rag_top_k,
            min_score=settings.rag_min_score,
        ),
    )
    run = AgentRun(
        id=run_id,
        ticket_id=ticket.id,
        graph_thread_id=thread_id,
        state=state.model_dump(mode="json"),
        provider_name=settings.llm_provider,
        model_name=settings.openai_model
        if settings.llm_provider == "openai"
        else "deterministic-access-v1",
    )
    session.add(run)
    ticket.status = TicketStatus.PROCESSING
    session.commit()
    session.refresh(run)
    return run


def get_run(session: Session, run_id: UUID) -> AgentRun:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise DomainError(404, "run_not_found", "Agent run does not exist.")
    return run


def claim_run(session: Session, owner: str, lease_seconds: int) -> AgentRun | None:
    now = utc_now()
    run = session.scalar(
        select(AgentRun)
        .where(
            or_(
                AgentRun.status == AgentRunStatus.PENDING,
                (AgentRun.status == AgentRunStatus.RUNNING)
                & or_(AgentRun.lease_expires_at <= now, AgentRun.lease_expires_at.is_(None)),
            )
        )
        .order_by(AgentRun.queued_at, AgentRun.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if run is None:
        return None
    run.status = AgentRunStatus.RUNNING
    run.lease_owner = owner
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    run.started_at = run.started_at or now
    run.attempt_count += 1
    run.recovery_attempt_count += 1
    session.commit()
    session.refresh(run)
    return run


def mark_waiting(session: Session, run_id: UUID, owner: str, state: AgentState) -> None:
    """Publish the pause only after LangGraph has persisted its interrupt."""
    run = owned_run(session, run_id, owner)
    ticket = session.scalar(select(Ticket).where(Ticket.id == run.ticket_id).with_for_update())
    approval = session.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == state.approval_id).with_for_update()
    )
    if approval is None or approval.run_id != run.id or approval.status != ApprovalStatus.PENDING:
        raise RuntimeError("A paused run must have its own pending approval.")
    step = session.scalar(
        select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.node == "AWAIT_APPROVAL")
    )
    if step is None:
        raise RuntimeError("Approval interrupt has no timeline step.")
    step.status = AgentStepStatus.WAITING
    step.summary = "Waiting for the assigned manager or an authorized administrator to review."
    step.latency_ms = 0
    state.current_node = WorkflowNode.AWAIT_APPROVAL
    run.state = state.model_dump(mode="json")
    run.status = AgentRunStatus.WAITING_FOR_APPROVAL
    ticket.status = TicketStatus.WAITING_FOR_APPROVAL
    run.lease_owner, run.lease_expires_at = None, None
    session.commit()


def owned_run(session: Session, run_id: UUID, owner: str) -> AgentRun:
    run = session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        run is None
        or run.status != AgentRunStatus.RUNNING
        or run.lease_owner != owner
        or run.lease_expires_at is None
        or run.lease_expires_at < utc_now()
    ):
        raise LeaseLost("Execution lease is no longer owned by this worker.")
    return run


def begin_step(
    session: Session,
    run_id: UUID,
    owner: str,
    sequence: int,
    node: str,
    lease_seconds: int,
) -> AgentStep:
    run = owned_run(session, run_id, owner)
    run.lease_expires_at = utc_now() + timedelta(seconds=lease_seconds)
    step = session.scalar(
        select(AgentStep).where(AgentStep.run_id == run_id, AgentStep.sequence == sequence)
    )
    if step is None:
        step = AgentStep(
            run_id=run_id,
            sequence=sequence,
            node=node,
            status=AgentStepStatus.RUNNING,
            summary=f"{node.replace('_', ' ').title()} started.",
        )
        session.add(step)
    session.commit()
    session.refresh(step)
    return step


def finish_step(
    session: Session,
    run_id: UUID,
    owner: str,
    step_id: UUID,
    state: AgentState,
    delta: dict,
    summary: str,
    latency_ms: int,
    lease_seconds: int,
) -> None:
    run = owned_run(session, run_id, owner)
    step = session.get(AgentStep, step_id)
    step.status = AgentStepStatus.FAILED if delta.get("error_code") else AgentStepStatus.COMPLETED
    step.summary = summary
    step.details = {"state_delta": delta}
    step.completed_at = utc_now()
    step.latency_ms = latency_ms
    run.state = state.model_dump(mode="json")
    run.lease_expires_at = utc_now() + timedelta(seconds=lease_seconds)
    session.commit()


def complete_run(session: Session, run_id: UUID, owner: str, state: AgentState) -> None:
    run = owned_run(session, run_id, owner)
    run.state = state.model_dump(mode="json")
    run.status = AgentRunStatus.FAILED if state.outcome == "failed" else AgentRunStatus.COMPLETED
    run.error = state.error_code
    run.completed_at = utc_now()
    run.lease_owner = None
    run.lease_expires_at = None
    session.commit()


def fail_exhausted(session: Session, run_id: UUID, owner: str, lease_seconds: int) -> None:
    step = begin_step(session, run_id, owner, 99, "ERROR", lease_seconds)
    run = owned_run(session, run_id, owner)
    ticket = session.get(Ticket, run.ticket_id)
    response = (
        "The agent could not complete after bounded recovery attempts. "
        "No successful access change is being reported. IT support must review this ticket."
    )
    ticket.status, ticket.final_response = TicketStatus.FAILED, response
    session.add(
        ConversationMessage(ticket_id=ticket.id, role=ConversationRole.ASSISTANT, content=response)
    )
    state = AgentState.model_validate(run.state)
    state.outcome, state.error_code, state.final_response = "failed", "recovery_exhausted", response
    run.state, run.status, run.error = (
        state.model_dump(mode="json"),
        AgentRunStatus.FAILED,
        state.error_code,
    )
    run.completed_at, run.lease_owner, run.lease_expires_at = utc_now(), None, None
    step.status, step.summary, step.completed_at = AgentStepStatus.FAILED, response, utc_now()
    session.commit()
