from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select

from app.api.dependencies import DBSession, PageLimit, PageOffset
from app.models import AgentRun, AgentStep, ToolExecution
from app.schemas.runs import RunAccepted, RunRead, StepRead, ToolExecutionRead
from app.schemas.tickets import Page
from app.services import runs
from app.services.tickets import get_ticket

router = APIRouter(tags=["agent runs"])


@router.post("/tickets/{ticket_id}/run", response_model=RunAccepted, status_code=202)
def submit_run(
    ticket_id: UUID, session: DBSession, request: Request, response: Response
) -> RunAccepted:
    run = runs.submit_run(session, ticket_id, request.app.state.settings)
    response.headers["Location"] = f"/api/agent-runs/{run.id}"
    return RunAccepted(run_id=run.id, ticket_id=run.ticket_id, status=run.status)


@router.get("/tickets/{ticket_id}/runs", response_model=Page[RunRead])
def list_ticket_runs(
    ticket_id: UUID,
    session: DBSession,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
) -> Page[RunRead]:
    get_ticket(session, ticket_id)
    query = select(AgentRun).where(AgentRun.ticket_id == ticket_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.order_by(AgentRun.queued_at.desc(), AgentRun.id).offset(offset).limit(limit)
    )
    return Page(
        items=[RunRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/agent-runs/{run_id}", response_model=RunRead)
def get_run(run_id: UUID, session: DBSession) -> RunRead:
    return RunRead.model_validate(runs.get_run(session, run_id))


@router.get("/agent-runs/{run_id}/steps", response_model=Page[StepRead])
def get_steps(
    run_id: UUID,
    session: DBSession,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
) -> Page[StepRead]:
    runs.get_run(session, run_id)
    query = select(AgentStep).where(AgentStep.run_id == run_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(query.order_by(AgentStep.sequence).offset(offset).limit(limit))
    return Page(
        items=[StepRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/agent-runs/{run_id}/tools", response_model=Page[ToolExecutionRead])
def get_tools(
    run_id: UUID,
    session: DBSession,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
) -> Page[ToolExecutionRead]:
    runs.get_run(session, run_id)
    query = select(ToolExecution).where(ToolExecution.run_id == run_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.order_by(ToolExecution.started_at, ToolExecution.id).offset(offset).limit(limit)
    )
    return Page(
        items=[ToolExecutionRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
