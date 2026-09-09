from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from app.api.demo_auth import CurrentUser
from app.api.dependencies import DBSession, PageLimit, PageOffset
from app.core.errors import DomainError
from app.evaluation import service
from app.models import EvaluationBatch, EvaluationResult, UserRole
from app.schemas.evaluations import (
    EvaluationBatchRead,
    EvaluationDetail,
    EvaluationRead,
    EvaluationScenarioRead,
    EvaluationSubmit,
    EvaluationSummary,
)
from app.schemas.tickets import Page

router = APIRouter(tags=["evaluations"])


def present(record: EvaluationResult) -> EvaluationRead:
    trace = record.snapshot.get("trace", {})
    ticket, state = trace.get("ticket", {}), trace.get("run", {}).get("state", {})
    return EvaluationRead(
        **{
            key: getattr(record, key)
            for key in (
                "id",
                "run_id",
                "batch_id",
                "source",
                "scenario_id",
                "evaluator_version",
                "created_at",
                "passed",
                "metrics",
                "assertions",
                "latency_ms",
            )
        },
        human_wait_ms=record.snapshot.get("human_wait_ms"),
        observed={
            "ticket_id": ticket.get("id"),
            "request_text": ticket.get("request_text"),
            "ticket_status": ticket.get("status"),
            "outcome": state.get("outcome"),
            "employee_name": (state.get("employee") or {}).get("name"),
            "repository_name": (state.get("repository") or {}).get("name"),
        },
    )


@router.get("/evaluations/scenarios", response_model=list[EvaluationScenarioRead])
def scenarios():
    from app.evaluation.scenarios import SCENARIOS

    fields = {
        "id",
        "name",
        "description",
        "expected_outcome",
        "expected_final_status",
        "expected_tools",
        "forbidden_tools",
        "requires_approval",
        "expected_permission",
        "expected_policy_sections",
    }
    return [item.model_dump(mode="json", include=fields) for item in SCENARIOS]


@router.post("/evaluations/run", status_code=202, response_model=EvaluationBatchRead)
def submit(data: EvaluationSubmit, actor: CurrentUser, session: DBSession, response: Response):
    if actor.role not in {UserRole.MANAGER, UserRole.ADMIN}:
        raise DomainError(
            403,
            "evaluation_role_required",
            "A manager or administrator must start scenario evaluations.",
        )
    batch = service.submit_batch(session, actor, data.scenario_ids, data.submission_key)
    response.headers["Location"] = f"/api/evaluations/batches/{batch.id}"
    return batch


@router.get("/evaluations/batches", response_model=Page[EvaluationBatchRead])
def batches(session: DBSession, limit: PageLimit = 20, offset: PageOffset = 0):
    query = select(EvaluationBatch).order_by(
        EvaluationBatch.created_at.desc(), EvaluationBatch.id.desc()
    )
    return Page(
        items=list(session.scalars(query.limit(limit).offset(offset))),
        total=session.scalar(select(func.count()).select_from(EvaluationBatch)),
        limit=limit,
        offset=offset,
    )


@router.get("/evaluations/batches/{batch_id}", response_model=EvaluationBatchRead)
def batch_detail(batch_id: UUID, session: DBSession):
    batch = session.get(EvaluationBatch, batch_id)
    if batch is None:
        raise DomainError(404, "evaluation_batch_not_found", "Evaluation batch does not exist.")
    return batch


@router.get("/evaluations/summary", response_model=EvaluationSummary)
def summary(
    session: DBSession, source: Literal["live", "scenario"] = "live", batch_id: UUID | None = None
):
    return service.summarize(session, source, batch_id)


@router.get("/evaluations/results", response_model=Page[EvaluationRead])
def results(
    session: DBSession,
    source: Literal["live", "scenario"] = "live",
    batch_id: UUID | None = None,
    run_id: UUID | None = None,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
):
    batch_id = service.selected_batch(session, source, batch_id)
    query = service.result_query(source, batch_id, run_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(
        query.order_by(EvaluationResult.created_at.desc(), EvaluationResult.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(items=[present(row) for row in rows], total=total, limit=limit, offset=offset)


@router.get("/evaluations/results/{result_id}", response_model=EvaluationDetail)
def result_detail(result_id: UUID, session: DBSession):
    record = session.get(EvaluationResult, result_id)
    if record is None:
        raise DomainError(404, "evaluation_not_found", "Evaluation result does not exist.")
    return EvaluationDetail(
        **present(record).model_dump(),
        trace=record.snapshot["trace"],
        expected=record.snapshot.get("expected"),
    )
