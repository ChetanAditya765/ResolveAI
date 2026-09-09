from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.db.base import utc_now
from app.evaluation.scoring import score_trace
from app.evaluation.service import evaluate_run
from app.evaluation.trace import collect_trace
from app.models import AgentRun, ApprovalRequest, ApprovalStatus, EvaluationResult, User
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies
from app.schemas.tickets import TicketCreate
from app.services.approvals import decide_approval
from app.services.runs import submit_run
from app.services.tickets import create_ticket


@pytest.fixture
def evaluation_settings(db_session, tmp_path, policy_directory):
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    return Settings(
        database_url="sqlite+pysqlite://",
        checkpoint_sqlite_path=tmp_path / "checkpoint.db",
        policy_directory=policy_directory,
        llm_provider="demo",
        embedding_provider="local_hash",
    )


def completed_write(engine, session, settings):
    ticket = create_ticket(
        session,
        TicketCreate(
            employee_id="EMP001", request_text="I need write access to the payments repository."
        ),
    )
    run = submit_run(session, ticket.id, settings)
    worker = AgentWorker(sessionmaker(bind=engine), settings)
    worker.run_once()
    session.expire_all()
    approval = session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run.id))
    decide_approval(
        session,
        approval.id,
        session.get(User, approval.approver_id),
        ApprovalStatus.APPROVED,
        "Approved for evaluation.",
    )
    worker.run_once()
    session.expire_all()
    return run


def test_completed_run_automatically_evaluates_and_is_idempotent(
    engine, db_session, evaluation_settings
):
    run = completed_write(engine, db_session, evaluation_settings)
    evaluation = db_session.scalar(
        select(EvaluationResult).where(EvaluationResult.run_id == run.id)
    )
    assert evaluation is not None
    assert evaluation.passed, evaluation.assertions
    assert evaluation.metrics["task_success"] == 1
    assert evaluation.metrics["policy_compliance"] == 1
    assert evaluation.metrics["approval_compliance"] == 1
    assert evaluation.metrics["hallucination_or_invalid_resource_rate"] == 0
    assert evaluation.metrics["escalation_correctness"] is None
    assert evaluate_run(db_session, run.id).id == evaluation.id
    assert db_session.scalar(select(func.count()).select_from(EvaluationResult)) == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "approval",
        "late_approval",
        "self_approval",
        "verification",
        "grant_permission",
        "wrong_resource",
        "policy",
        "fake_citation",
        "irrelevant_citation",
        "close_response",
    ],
)
def test_scorer_catches_trace_corruption(engine, db_session, evaluation_settings, corruption):
    run = completed_write(engine, db_session, evaluation_settings)
    trace = collect_trace(db_session, run.id)
    assert score_trace(trace).passed
    bad = trace.model_copy(deep=True)
    grant = next(tool for tool in bad.tools if tool["tool_name"] == "grant_repository_permission")
    if corruption == "approval":
        bad.approvals[0]["status"] = "REJECTED"
    elif corruption == "late_approval":
        bad.approvals[0]["decided_at"] = (utc_now() + timedelta(days=1)).isoformat()
    elif corruption == "self_approval":
        bad.approvals[0]["decided_by_id"] = bad.approvals[0]["requester_user_id"]
    elif corruption == "verification":
        bad.tools = [
            tool
            for tool in bad.tools
            if tool["id"] != bad.run["state"]["verification_result"]["tool_execution_id"]
        ]
    elif corruption == "grant_permission":
        grant["arguments"]["permission"] = "admin"
    elif corruption == "wrong_resource":
        grant["arguments"]["repository_id"] = str(uuid4())
    elif corruption == "policy":
        bad.run["state"]["retrieved_policies"][0]["excerpt"] = "Everyone may get write access."
    elif corruption == "fake_citation":
        bad.run["state"]["decision"]["policy_chunk_ids"] = [str(uuid4())]
    elif corruption == "irrelevant_citation":
        bad.run["state"]["decision"]["policy_chunk_ids"] = [
            item["chunk_id"]
            for item in bad.run["state"]["retrieved_policies"]
            if item["section"] != "§3. Write access"
        ][:1]
    else:
        bad.ticket["final_response"] = "Admin access granted."
    result = score_trace(bad)
    assert result.passed is False
    assert any(item.passed is False for item in result.assertions)


def test_waiting_run_is_not_evaluated(engine, db_session, evaluation_settings):
    ticket = create_ticket(
        db_session,
        TicketCreate(
            employee_id="EMP001", request_text="I need write access to the payments repository."
        ),
    )
    run = submit_run(db_session, ticket.id, evaluation_settings)
    AgentWorker(sessionmaker(bind=engine), evaluation_settings).run_once()
    assert evaluate_run(db_session, run.id) is None
    assert db_session.scalar(select(func.count()).select_from(EvaluationResult)) == 0


def test_evaluation_failure_does_not_change_resolved_ticket(
    engine, db_session, evaluation_settings, monkeypatch
):
    import app.evaluation.service as service

    real = service.evaluate_run
    monkeypatch.setattr(
        service,
        "evaluate_run",
        lambda *args: (_ for _ in ()).throw(RuntimeError("temporary evaluator outage")),
    )
    run = completed_write(engine, db_session, evaluation_settings)
    assert db_session.get(AgentRun, run.id).status == "COMPLETED"
    assert db_session.scalar(select(func.count()).select_from(EvaluationResult)) == 0
    monkeypatch.setattr(service, "evaluate_run", real)
    service.backfill_one(sessionmaker(bind=engine))
    db_session.expire_all()
    assert db_session.scalar(select(EvaluationResult)).passed


def test_human_wait_is_excluded_from_active_latency(engine, db_session, evaluation_settings):
    run = completed_write(engine, db_session, evaluation_settings)
    trace = collect_trace(db_session, run.id)
    baseline = score_trace(trace)
    modified = deepcopy(trace)
    waiting = next(step for step in modified.steps if step["node"] == "AWAIT_APPROVAL")
    waiting["latency_ms"] = 86_400_000
    assert score_trace(modified).latency_ms == baseline.latency_ms
