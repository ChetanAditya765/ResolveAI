import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.evaluation.scoring import score_trace
from app.evaluation.trace import collect_trace
from app.models import AgentRun, ApprovalRequest, ApprovalStatus, EvaluationResult, User
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies
from app.schemas.tickets import TicketCreate
from app.services.approvals import decide_approval
from app.services.runs import submit_run
from app.services.tickets import create_ticket


@pytest.fixture
def concurrent_access(engine, db_session, tmp_path, policy_directory):
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    settings = Settings(
        database_url="sqlite+pysqlite://",
        checkpoint_sqlite_path=tmp_path / "checkpoints.sqlite",
        policy_directory=policy_directory,
        llm_provider="demo",
        embedding_provider="local_hash",
    )
    worker = AgentWorker(sessionmaker(bind=engine), settings)
    run_ids = []
    for permission in ("read", "write"):
        ticket = create_ticket(
            db_session,
            TicketCreate(
                employee_id="EMP001",
                request_text=f"I need {permission} access to the finance-reporting repository.",
            ),
        )
        run_ids.append(submit_run(db_session, ticket.id, settings).id)
        worker.run_once()
    # Both runs observed no access. The write approval is processed before the read approval.
    for run_id in reversed(run_ids):
        db_session.expire_all()
        approval = db_session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.run_id == run_id)
        )
        assert approval is not None
        decide_approval(
            db_session,
            approval.id,
            db_session.get(User, approval.approver_id),
            ApprovalStatus.APPROVED,
            "Approved for the assigned work.",
        )
        worker.run_once()
    db_session.expire_all()
    return run_ids


def test_prior_write_satisfies_pending_read_without_false_evaluation_failure(
    db_session, concurrent_access
):
    for run_id in concurrent_access:
        evaluation = db_session.scalar(
            select(EvaluationResult).where(EvaluationResult.run_id == run_id)
        )
        assert evaluation is not None and evaluation.passed, evaluation.assertions
        assert evaluation.metrics["task_success"] == 1
        assert evaluation.metrics["policy_compliance"] == 1
        assert evaluation.metrics["approval_compliance"] == 1
    read_run = db_session.get(AgentRun, concurrent_access[0])
    assert read_run.state["outcome"] == "already_sufficient"
    assert read_run.state["execution_result"]["changed"] is False
    assert read_run.state["verification_result"]["observed_permission"] == "write"
    assert read_run.state["current_permission"] == "none"


@pytest.mark.parametrize("corruption", ["claimed_change", "wrong_previous", "downgrade"])
def test_no_change_grants_require_unchanged_sufficient_permission(
    db_session, concurrent_access, corruption
):
    trace = collect_trace(db_session, concurrent_access[0])
    assert score_trace(trace).passed
    grant = next(t for t in trace.tools if t["tool_name"] == "grant_repository_permission")
    result = grant["result"]["tool_result"]["output"]
    assert result["changed"] is False
    assert result["previous_permission"] == result["observed_permission"] == "write"
    if corruption == "claimed_change":
        result["changed"] = True
    elif corruption == "wrong_previous":
        result["previous_permission"] = "none"
    else:
        result["observed_permission"] = "read"
    score = score_trace(trace)
    assert not score.passed
    assert score.metrics["policy_compliance"] == 0
    assert score.metrics["task_success"] == 0
    assert next(a for a in score.assertions if a.name == "least_privilege").passed is False


@pytest.mark.parametrize("previous", ["write", "admin", "unknown"])
def test_actual_grants_require_an_increase_from_a_valid_previous_permission(
    db_session, concurrent_access, previous
):
    trace = collect_trace(db_session, concurrent_access[1])
    assert score_trace(trace).passed
    grant = next(t for t in trace.tools if t["tool_name"] == "grant_repository_permission")
    result = grant["result"]["tool_result"]["output"]
    assert result["changed"] is True
    result["previous_permission"] = previous
    score = score_trace(trace)
    assert not score.passed
    assert next(a for a in score.assertions if a.name == "least_privilege").passed is False
