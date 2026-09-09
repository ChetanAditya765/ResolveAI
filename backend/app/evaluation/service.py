import logging
from datetime import timedelta
from statistics import mean
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import DomainError
from app.db.base import utc_now
from app.evaluation.contracts import CATALOG_VERSION, EVALUATOR_VERSION, ExpectedOutcome
from app.evaluation.scoring import score_trace
from app.evaluation.trace import collect_trace
from app.models import AgentRun, AgentRunStatus, EvaluationBatch, EvaluationResult, User

logger = logging.getLogger("resolveai.evaluation")
TERMINAL = (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED)
METRICS = (
    "task_success",
    "tool_selection_accuracy",
    "policy_compliance",
    "approval_compliance",
    "escalation_correctness",
    "hallucination_or_invalid_resource_rate",
)


def evaluate_run(session: Session, run_id: UUID) -> EvaluationResult | None:
    run = session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if run is None:
        raise DomainError(404, "run_not_found", "Agent run does not exist.")
    if run.status not in TERMINAL or run.state.get("workflow_version", 1) < 3:
        return None
    existing = session.scalar(
        select(EvaluationResult).where(
            EvaluationResult.run_id == run_id,
            EvaluationResult.evaluator_version == EVALUATOR_VERSION,
        )
    )
    if existing:
        return existing
    trace = collect_trace(session, run_id)
    score = score_trace(trace)
    record = EvaluationResult(
        source="live",
        scenario_id="live_run",
        run_id=run_id,
        evaluator_version=EVALUATOR_VERSION,
        metrics=score.metrics,
        assertions=[item.model_dump(mode="json") for item in score.assertions],
        passed=score.passed,
        latency_ms=score.latency_ms,
        snapshot={
            "trace": trace.model_dump(mode="json"),
            "expected": None,
            "human_wait_ms": score.human_wait_ms,
        },
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def pending_live_query():
    return (
        select(AgentRun.id)
        .where(
            AgentRun.status.in_(TERMINAL),
            AgentRun.state["workflow_version"].as_integer() >= 3,
            ~exists().where(
                EvaluationResult.run_id == AgentRun.id,
                EvaluationResult.evaluator_version == EVALUATOR_VERSION,
            ),
        )
        .order_by(AgentRun.completed_at, AgentRun.id)
    )


def backfill_one(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        run_id = session.scalar(pending_live_query().limit(1))
        if run_id:
            evaluate_run(session, run_id)


def submit_batch(
    session: Session, actor: User, scenario_ids: list[str] | None, submission_key: UUID
) -> EvaluationBatch:
    from app.evaluation.scenarios import SCENARIOS

    available = {item.id for item in SCENARIOS}
    selected = sorted(scenario_ids if scenario_ids is not None else available)
    if not selected or len(selected) != len(set(selected)) or set(selected) - available:
        raise DomainError(
            422, "invalid_scenarios", "Select unique scenario IDs from the evaluation catalog."
        )
    existing = session.scalar(
        select(EvaluationBatch).where(EvaluationBatch.submission_key == submission_key)
    )
    if existing:
        if existing.requested_by_id != actor.id or existing.scenario_ids != selected:
            raise DomainError(
                409,
                "evaluation_key_conflict",
                "Submission key was already used for another evaluation.",
            )
        return existing
    active = session.scalar(select(EvaluationBatch).where(EvaluationBatch.active_slot == 1))
    if active:
        raise DomainError(
            409,
            "evaluation_batch_active",
            "An evaluation batch is already active. Follow its progress before starting another.",
        )
    batch = EvaluationBatch(
        requested_by_id=actor.id,
        submission_key=submission_key,
        scenario_ids=selected,
        catalog_version=CATALOG_VERSION,
        evaluator_version=EVALUATOR_VERSION,
        total_count=len(selected),
        active_slot=1,
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return batch


def fail_batch(batch: EvaluationBatch, message: str) -> None:
    batch.status, batch.error, batch.completed_at = "FAILED", message, utc_now()
    batch.active_slot, batch.lease_owner, batch.lease_expires_at = None, None, None


def process_next(sessions: sessionmaker[Session], settings: Settings, owner: str) -> UUID | None:
    """Claim and execute one case, yielding the worker to live requests between cases."""
    from app.evaluation.harness import run_scenario
    from app.evaluation.scenarios import SCENARIOS

    catalog = {item.id: item for item in SCENARIOS}
    with sessions() as session:
        batch = session.scalar(
            select(EvaluationBatch)
            .where(
                EvaluationBatch.active_slot == 1,
                or_(
                    EvaluationBatch.lease_expires_at.is_(None),
                    EvaluationBatch.lease_expires_at <= utc_now(),
                ),
            )
            .order_by(EvaluationBatch.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if batch is None:
            return None
        batch_id = batch.id
        if batch.evaluator_version != EVALUATOR_VERSION:
            fail_batch(
                batch,
                "The recorded evaluator version is unavailable. Start a new evaluation batch.",
            )
            session.commit()
            return batch_id
        if batch.catalog_version != CATALOG_VERSION or batch.attempt_count >= 3:
            fail_batch(
                batch, "Scenario execution could not recover with the recorded catalog version."
            )
            session.commit()
            return batch_id
        done = set(
            session.scalars(
                select(EvaluationResult.scenario_id).where(
                    EvaluationResult.batch_id == batch.id,
                    EvaluationResult.evaluator_version == batch.evaluator_version,
                )
            )
        )
        remaining = [key for key in batch.scenario_ids if key not in done]
        if not remaining:
            batch.status, batch.completed_count, batch.completed_at = (
                "COMPLETED",
                len(done),
                utc_now(),
            )
            batch.active_slot, batch.lease_owner, batch.lease_expires_at = None, None, None
            session.commit()
            return batch_id
        scenario_id = remaining[0]
        if scenario_id not in catalog:
            fail_batch(batch, "A recorded scenario is unavailable in this application version.")
            session.commit()
            return batch_id
        batch.status, batch.started_at = "RUNNING", batch.started_at or utc_now()
        batch.lease_owner, batch.lease_expires_at = owner, utc_now() + timedelta(seconds=180)
        batch.attempt_count += 1
        session.commit()
    try:
        scenario = catalog[scenario_id]
        trace = run_scenario(scenario, settings)
        expected = ExpectedOutcome.model_validate(scenario.model_dump(mode="json"))
        score = score_trace(trace, expected)
        with sessions() as session:
            batch = session.scalar(
                select(EvaluationBatch).where(EvaluationBatch.id == batch_id).with_for_update()
            )
            if (
                batch.lease_owner != owner
                or batch.lease_expires_at is None
                or batch.lease_expires_at < utc_now()
            ):
                return batch_id
            if batch.evaluator_version != EVALUATOR_VERSION:
                fail_batch(
                    batch,
                    "The recorded evaluator version changed during execution. "
                    "Start a new evaluation batch.",
                )
                session.commit()
                return batch_id
            session.add(
                EvaluationResult(
                    source="scenario",
                    run_id=None,
                    batch_id=batch_id,
                    scenario_id=scenario_id,
                    evaluator_version=EVALUATOR_VERSION,
                    metrics=score.metrics,
                    assertions=[item.model_dump(mode="json") for item in score.assertions],
                    passed=score.passed,
                    latency_ms=score.latency_ms,
                    snapshot={
                        "trace": trace.model_dump(mode="json"),
                        "expected": expected.model_dump(mode="json"),
                        "human_wait_ms": score.human_wait_ms,
                    },
                )
            )
            batch.completed_count += 1
            batch.attempt_count = 0
            batch.lease_owner, batch.lease_expires_at = None, None
            if batch.completed_count == batch.total_count:
                batch.status, batch.completed_at, batch.active_slot = "COMPLETED", utc_now(), None
            session.commit()
    except Exception as exc:
        # Leave the lease for bounded recovery; never manufacture successful case results.
        logger.error(
            "scenario_execution_failed",
            extra={"scenario_id": scenario_id, "error_type": type(exc).__name__},
        )
    return batch_id


def selected_batch(session: Session, source: str, batch_id: UUID | None) -> UUID | None:
    if source == "live":
        if batch_id is not None:
            raise DomainError(
                422, "invalid_evaluation_filter", "Live results do not belong to scenario batches."
            )
        return None
    if batch_id is not None:
        if session.get(EvaluationBatch, batch_id) is None:
            raise DomainError(404, "evaluation_batch_not_found", "Evaluation batch does not exist.")
        return batch_id
    return session.scalar(
        select(EvaluationBatch.id)
        .order_by(EvaluationBatch.created_at.desc(), EvaluationBatch.id.desc())
        .limit(1)
    )


def result_query(source: str, batch_id: UUID | None, run_id: UUID | None = None):
    query = select(EvaluationResult).where(EvaluationResult.source == source)
    if source == "scenario":
        query = query.join(EvaluationBatch, EvaluationResult.batch_id == EvaluationBatch.id).where(
            EvaluationResult.batch_id == batch_id,
            EvaluationResult.evaluator_version == EvaluationBatch.evaluator_version,
        )
    else:
        query = query.where(EvaluationResult.evaluator_version == EVALUATOR_VERSION)
    if run_id is not None:
        query = query.where(EvaluationResult.run_id == run_id)
    return query


def summarize(session: Session, source: str, batch_id: UUID | None) -> dict:
    from app.evaluation.scenarios import SCENARIOS

    batch_id = selected_batch(session, source, batch_id)
    query = result_query(source, batch_id).with_only_columns(
        EvaluationResult.metrics,
        EvaluationResult.latency_ms,
        EvaluationResult.passed,
        EvaluationResult.snapshot["human_wait_ms"].as_integer(),
    )
    rows = session.execute(query).all()
    values = {
        name: [row[0][name] for row in rows if row[0].get(name) is not None] for name in METRICS
    }
    latencies = [row[1] for row in rows if row[1] is not None]
    waits = [row[3] for row in rows if row[3] is not None]
    batch = session.get(EvaluationBatch, batch_id) if batch_id else None
    return {
        "source": source,
        "batch_id": batch_id,
        "evaluator_version": batch.evaluator_version if batch else EVALUATOR_VERSION,
        "total_scenarios": len(SCENARIOS),
        "total_results": len(rows),
        "passed_results": sum(row[2] for row in rows),
        "selected_scenarios": batch.total_count if batch else None,
        "metrics": {name: mean(series) if series else None for name, series in values.items()},
        "metric_samples": {name: len(series) for name, series in values.items()},
        "average_latency_ms": mean(latencies) if latencies else None,
        "average_human_wait_ms": mean(waits) if waits else None,
    }
