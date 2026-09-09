from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.core.errors import DomainError
from app.db.base import utc_now
from app.models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    ConversationMessage,
    RepositoryPermission,
    Ticket,
    TicketStatus,
    ToolExecution,
)
from app.providers import DemoProvider, ProviderError
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies
from app.schemas.tickets import TicketCreate
from app.services import runs
from app.services.tickets import create_ticket


@pytest.fixture
def run_settings(tmp_path: Path, db_session: Session) -> Settings:
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    db_session.commit()
    return Settings(
        database_url="sqlite+pysqlite://",
        app_env="test",
        checkpoint_sqlite_path=tmp_path / "checkpoints.sqlite",
        agent_worker_enabled=False,
    )


def submitted(
    session: Session,
    settings: Settings,
    request: str = "I need write access to the payments repository.",
) -> AgentRun:
    ticket = create_ticket(session, TicketCreate(employee_id="EMP001", request_text=request))
    run = runs.submit_run(session, ticket.id, settings)
    # Preserve explicit coverage of the previously shipped graph during recovery.
    run.state = {**run.state, "workflow_version": 2}
    session.commit()
    return run


def test_graph_records_lookup_tools_and_safe_phase_boundary(
    engine, db_session: Session, run_settings: Settings
):
    run = submitted(db_session, run_settings)
    before = {row.id: row.permission for row in db_session.scalars(select(RepositoryPermission))}
    worker = AgentWorker(sessionmaker(bind=engine), run_settings)
    assert worker.run_once() == run.id
    db_session.expire_all()
    finished = db_session.get(AgentRun, run.id)
    assert finished.status == AgentRunStatus.COMPLETED
    assert finished.state["repository"]["name"] == "payments"
    assert finished.state["employee"]["id"] == "EMP001"
    assert finished.state["requested_permission"] == "write"
    assert finished.state["current_permission"] == "read"
    assert finished.state["retrieved_policies"]
    assert finished.state["decision"]["disposition"] == "approval_required"
    assert "Repository Access Policy §3" in finished.state["decision"]["summary"]
    assert finished.state["outcome"] == "escalated"
    assert "not enabled" in finished.state["final_response"]
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.ESCALATED
    steps = db_session.scalars(
        select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)
    ).all()
    assert [step.node for step in steps] == [
        "RECEIVE_REQUEST",
        "IDENTIFY_EMPLOYEE",
        "CLASSIFY_REQUEST",
        "IDENTIFY_RESOURCE",
        "CHECK_CURRENT_ACCESS",
        "RETRIEVE_POLICY",
        "PLAN_ACTION",
        "ESCALATE",
    ]
    assert all(step.completed_at and step.latency_ms is not None for step in steps)
    tools = db_session.scalars(select(ToolExecution).where(ToolExecution.run_id == run.id)).all()
    assert {tool.tool_name for tool in tools} == {
        "get_employee",
        "find_repository",
        "get_repository_permission",
        "escalate_ticket",
        "search_policies",
    }
    assert {
        row.id: row.permission for row in db_session.scalars(select(RepositoryPermission))
    } == before
    assert worker.run_once() is None


@pytest.mark.parametrize(
    "request_text,expected_error,forbidden",
    [
        (
            "I need write access to missing-repo.",
            "repository_not_found",
            "get_repository_permission",
        ),
        ("I need access to payments", "clarification_required", "find_repository"),
        ("Reset my laptop", "unsupported_request", "find_repository"),
        ("Grant me admin access to payments", None, "grant_repository_permission"),
    ],
)
def test_safe_routes(engine, db_session, run_settings, request_text, expected_error, forbidden):
    run = submitted(db_session, run_settings, request_text)
    AgentWorker(sessionmaker(bind=engine), run_settings).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.state["error_code"] == expected_error
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.ESCALATED
    assert (
        forbidden
        not in db_session.scalars(
            select(ToolExecution.tool_name).where(ToolExecution.run_id == run.id)
        ).all()
    )


def test_timeout_fails_run_and_never_claims_success(engine, db_session, run_settings):
    class TimeoutProvider(DemoProvider):
        def classify(self, request_text):
            raise ProviderError("llm_timeout", "safe timeout")

    run = submitted(db_session, run_settings)
    AgentWorker(sessionmaker(bind=engine), run_settings, TimeoutProvider()).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.status == AgentRunStatus.FAILED
    assert done.error == "llm_timeout"
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.ESCALATED


def test_duplicate_submission_reuses_active_run(db_session, run_settings):
    run = submitted(db_session, run_settings)
    duplicate = runs.submit_run(db_session, run.ticket_id, run_settings)
    assert duplicate.id == run.id
    assert db_session.scalar(select(func.count()).select_from(AgentRun)) == 1


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_unconfigured_openai_rejects_submission_without_creating_run(db_session, api_key):
    settings = Settings(llm_provider="openai", openai_api_key=api_key, agent_worker_enabled=False)
    ticket = create_ticket(
        db_session, TicketCreate(employee_id="EMP001", request_text="Read access to payments")
    )
    with pytest.raises(DomainError) as caught:
        runs.submit_run(db_session, ticket.id, settings)
    assert caught.value.status_code == 503
    assert db_session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert db_session.get(Ticket, ticket.id).status == TicketStatus.OPEN


def expire_lease(session: Session, run_id: UUID) -> None:
    session.expire_all()
    run = session.get(AgentRun, run_id)
    run.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.commit()


def test_recovery_after_graph_completion_does_not_repeat_tools(
    engine,
    db_session,
    run_settings,
    monkeypatch,
):
    run = submitted(db_session, run_settings)
    worker = AgentWorker(sessionmaker(bind=engine), run_settings)
    original = runs.complete_run
    monkeypatch.setattr(
        runs, "complete_run", lambda *args: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    worker.run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).status == AgentRunStatus.RUNNING
    tool_count = db_session.scalar(select(func.count()).select_from(ToolExecution))
    message_count = db_session.scalar(select(func.count()).select_from(ConversationMessage))
    monkeypatch.setattr(runs, "complete_run", original)
    expire_lease(db_session, run.id)
    AgentWorker(sessionmaker(bind=engine), run_settings).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).status == AgentRunStatus.COMPLETED
    assert db_session.get(AgentRun, run.id).attempt_count == 2
    assert db_session.scalar(select(func.count()).select_from(ToolExecution)) == tool_count
    assert db_session.scalar(select(func.count()).select_from(ConversationMessage)) == message_count


def test_live_lease_cannot_be_stolen(db_session, run_settings):
    run = submitted(db_session, run_settings)
    assert runs.claim_run(db_session, "first", 180).id == run.id
    assert runs.claim_run(db_session, "second", 180) is None
    with pytest.raises(runs.LeaseLost):
        runs.owned_run(db_session, run.id, "second")


def test_exhausted_recovery_marks_ticket_failed(engine, db_session, run_settings):
    run = submitted(db_session, run_settings)
    run.attempt_count = run_settings.agent_max_attempts
    db_session.commit()
    AgentWorker(sessionmaker(bind=engine), run_settings).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).status == AgentRunStatus.FAILED
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.FAILED


def test_run_api_exposes_paginated_trace(client, engine, db_session, run_settings):
    created = client.post(
        "/api/tickets",
        json={"employee_id": "EMP001", "request_text": "I need write access to payments."},
    ).json()
    response = client.post(f"/api/tickets/{created['id']}/run")
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert client.post(f"/api/tickets/{created['id']}/run").json()["run_id"] == run_id
    AgentWorker(sessionmaker(bind=engine), run_settings).run_once()
    run_response = client.get(f"/api/agent-runs/{run_id}")
    assert run_response.status_code == 200
    assert run_response.json()["status"] == "WAITING_FOR_APPROVAL"
    assert "lease_owner" not in run_response.json()
    assert client.get(f"/api/agent-runs/{run_id}/steps?limit=2").json()["total"] == 9
    assert client.get(f"/api/agent-runs/{run_id}/tools").json()["total"] == 6
    assert client.get(f"/api/tickets/{created['id']}/runs").json()["total"] == 1
    assert client.get(f"/api/agent-runs/{uuid4()}").status_code == 404
    assert client.post(f"/api/tickets/{created['id']}/run").status_code == 202
