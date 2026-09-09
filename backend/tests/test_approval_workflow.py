from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.core.errors import DomainError
from app.db.base import utc_now
from app.models import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
    Employee,
    KnowledgeDocument,
    Permission,
    Repository,
    RepositoryPermission,
    Ticket,
    TicketStatus,
    ToolExecution,
    User,
    UserRole,
)
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies
from app.schemas.tickets import TicketCreate
from app.services import runs
from app.services.approvals import decide_approval
from app.services.runs import submit_run
from app.services.tickets import create_ticket
from app.tools import access
from app.tools.access_schemas import CloseRequest, GrantRequest
from app.tools.domain import ToolError
from app.tools.runner import TOOL_REGISTRY
from app.tools.schemas import PermissionLookup


def login(client, employee_id):
    user = next(
        item for item in client.get("/api/demo/users").json() if item["employee_id"] == employee_id
    )
    token = client.post("/api/demo/session", json={"user_id": user["id"]}).json()["access_token"]
    return {"Authorization": "Bearer " + token}


def paused_run(engine, session, settings):
    run = begin(session, settings)
    AgentWorker(sessionmaker(bind=engine), settings).run_once()
    session.expire_all()
    assert session.get(AgentRun, run.id).status == AgentRunStatus.WAITING_FOR_APPROVAL
    approval = session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run.id))
    return run, approval


def approve_request(session, approval):
    return decide_approval(
        session,
        approval.id,
        session.get(User, approval.approver_id),
        ApprovalStatus.APPROVED,
        "Approved.",
    )


def expire_lease(session, run_id):
    session.expire_all()
    session.get(AgentRun, run_id).lease_expires_at = utc_now() - timedelta(seconds=1)
    session.commit()


def test_approval_api_authorization_and_immutable_decision(
    client, engine, db_session, flow_settings
):
    run, approval = paused_run(engine, db_session, flow_settings)
    path = f"/api/approvals/{approval.id}"
    assert client.get("/api/approvals").status_code == 401
    assert client.post(path + "/approve", json={}).status_code == 401
    employee = login(client, "EMP001")
    manager = login(client, "EMP002")
    other = login(client, "EMP005")
    assert client.get(path, headers=employee).status_code == 200
    assert client.get(path, headers=other).status_code == 403
    assert client.get("/api/approvals", headers=other).json()["total"] == 0
    assert client.post(path + "/approve", headers=employee, json={}).status_code == 403
    assert client.post(path + "/approve", headers=other, json={}).status_code == 403
    detail = client.get(path, headers=manager).json()
    assert detail["can_decide"]
    assert detail["employee"]["id"] == "EMP001"
    assert detail["repository"]["name"] == "payments"
    assert detail["policy_evidence"]
    accepted = client.post(path + "/approve", headers=manager, json={"comment": "Valid need."})
    assert accepted.status_code == 200, accepted.text
    original = accepted.json()
    again = client.post(path + "/approve", headers=manager, json={"comment": "Changed note"}).json()
    assert again["decided_at"] == original["decided_at"]
    assert again["decision_comment"] == "Valid need."
    assert client.post(path + "/reject", headers=manager, json={}).status_code == 409
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    assert client.get(f"/api/agent-runs/{run.id}").json()["state"]["outcome"] == "granted"
    assert client.post(path + "/approve", headers=manager, json={}).status_code == 200
    assert client.get(f"/api/agent-runs/{run.id}").json()["status"] == "COMPLETED"


def test_admin_may_review_but_cannot_self_approve(client, engine, db_session, flow_settings):
    _, approval = paused_run(engine, db_session, flow_settings)
    admin = login(client, "EMP003")
    assert (
        client.post(f"/api/approvals/{approval.id}/reject", headers=admin, json={}).status_code
        == 200
    )
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    run = begin(db_session, flow_settings, employee_id="EMP003")
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    db_session.expire_all()
    own = db_session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run.id))
    assert own is not None
    assert (
        client.post(f"/api/approvals/{own.id}/approve", headers=admin, json={}).status_code == 403
    )


@pytest.mark.parametrize(
    "change", ["inactive", "manager", "policy", "reviewer_inactive", "reviewer_role"]
)
def test_grant_revalidates_changed_eligibility(engine, db_session, flow_settings, change):
    run, approval = paused_run(engine, db_session, flow_settings)
    approve_request(db_session, approval)
    if change == "inactive":
        db_session.get(Employee, "EMP001").is_active = False
    elif change == "manager":
        db_session.get(Employee, "EMP001").manager_id = "EMP005"
    elif change == "reviewer_inactive":
        db_session.get(Employee, "EMP002").is_active = False
    elif change == "reviewer_role":
        db_session.get(User, approval.approver_id).role = UserRole.EMPLOYEE
    else:
        document = db_session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.slug == "repository-access-policy")
        )
        document.content += "\nPolicy changed during review.\n"
    db_session.commit()
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.state["outcome"] != "granted"
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.ESCALATED
    assert permission(db_session) == Permission.READ


def test_same_department_read_can_be_granted_automatically(engine, db_session, flow_settings):
    run = begin(db_session, flow_settings, "Read access to payments", "EMP007")
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.state["outcome"] == "granted", done.state
    assert permission(db_session, "EMP007") == Permission.READ
    assert (
        db_session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run.id)) is None
    )


def test_grant_tool_rejects_pending_approval_even_with_valid_scope(
    engine, db_session, flow_settings
):
    run, approval = paused_run(engine, db_session, flow_settings)
    run.status = AgentRunStatus.RUNNING
    db_session.get(Ticket, run.ticket_id).status = TicketStatus.PROCESSING
    db_session.commit()
    with pytest.raises(ToolError) as caught:
        access.grant_repository_permission(
            db_session,
            GrantRequest(
                employee_id="EMP001",
                repository_id=approval.repository_id,
                permission=Permission.WRITE,
                approval_id=approval.id,
            ),
            run_id=run.id,
        )
    assert caught.value.code == "approval_not_granted"
    db_session.rollback()
    assert permission(db_session) == Permission.READ


def test_close_tool_rejects_forged_verification_flag(engine, db_session, flow_settings):
    run, approval = paused_run(engine, db_session, flow_settings)
    approve_request(db_session, approval)
    run.status = AgentRunStatus.RUNNING
    record = db_session.scalar(
        select(RepositoryPermission).where(
            RepositoryPermission.employee_id == "EMP001",
            RepositoryPermission.repository_id == approval.repository_id,
        )
    )
    record.permission = Permission.WRITE
    run.state = {
        **run.state,
        "verification_result": {
            "tool_execution_id": str(uuid4()),
            "observed_permission": "write",
            "sufficient": True,
        },
    }
    db_session.commit()
    with pytest.raises(ToolError) as caught:
        access.close_ticket(db_session, CloseRequest(ticket_id=run.ticket_id), run_id=run.id)
    assert caught.value.code == "verification_invalid"
    db_session.rollback()
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.PROCESSING


def test_resume_does_not_consume_crash_recovery_budget(engine, db_session, flow_settings):
    settings = flow_settings.model_copy(update={"agent_max_attempts": 1})
    run, approval = paused_run(engine, db_session, settings)
    approve_request(db_session, approval)
    AgentWorker(sessionmaker(bind=engine), settings).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).state["outcome"] == "granted"


@pytest.mark.parametrize("crash_node", ["EXECUTE_ACTION", "RESOLVE"])
def test_recovery_after_committed_action_does_not_duplicate_effects(
    engine, db_session, flow_settings, monkeypatch, crash_node
):
    run, approval = paused_run(engine, db_session, flow_settings)
    approve_request(db_session, approval)
    original = runs.finish_step

    def crash(*args, **kwargs):
        if args[4].current_node == crash_node:
            raise RuntimeError("Crash after tool commit")
        return original(*args, **kwargs)

    monkeypatch.setattr(runs, "finish_step", crash)
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    monkeypatch.setattr(runs, "finish_step", original)
    expire_lease(db_session, run.id)
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).state["outcome"] == "granted"
    names = db_session.scalars(
        select(ToolExecution.tool_name).where(ToolExecution.run_id == run.id)
    ).all()
    assert names.count("grant_repository_permission") == 1
    assert names.count("close_ticket") == 1


def test_checkpoint_interrupt_recovers_before_pause_publication(
    engine, db_session, flow_settings, monkeypatch
):
    run = begin(db_session, flow_settings)
    original = runs.mark_waiting
    monkeypatch.setattr(
        runs, "mark_waiting", lambda *args: (_ for _ in ()).throw(RuntimeError("Crash"))
    )
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).status == AgentRunStatus.RUNNING
    approval = db_session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run.id))
    with pytest.raises(DomainError) as caught:
        approve_request(db_session, approval)
    assert caught.value.code == "approval_not_ready"
    db_session.rollback()
    monkeypatch.setattr(runs, "mark_waiting", original)
    expire_lease(db_session, run.id)
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).status == AgentRunStatus.WAITING_FOR_APPROVAL
    assert (
        len(
            db_session.scalars(
                select(ApprovalRequest).where(ApprovalRequest.run_id == run.id)
            ).all()
        )
        == 1
    )


@pytest.mark.parametrize("fault", ["grant", "verification"])
def test_tool_failure_never_falsely_resolves(engine, db_session, flow_settings, monkeypatch, fault):
    run, approval = paused_run(engine, db_session, flow_settings)
    approve_request(db_session, approval)
    if fault == "grant":
        original = access.grant_repository_permission

        def fail(session, args, **kwargs):
            original(session, args, **kwargs)
            raise ToolError("tool_failed", "Simulated service failure.")

        monkeypatch.setattr(access, "grant_repository_permission", fail)
    else:

        def fail(session, args):
            raise ToolError("tool_failed", "Simulated verification failure.")

        monkeypatch.setitem(TOOL_REGISTRY, "get_repository_permission", (PermissionLookup, fail))
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    db_session.expire_all()
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.ESCALATED
    done = db_session.get(AgentRun, run.id)
    assert done.status == AgentRunStatus.FAILED
    assert done.state["outcome"] != "granted"
    assert permission(db_session) == (Permission.READ if fault == "grant" else Permission.WRITE)


@pytest.fixture
def flow_settings(db_session, tmp_path: Path):
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    db_session.commit()
    return Settings(
        database_url="sqlite+pysqlite://",
        agent_worker_enabled=False,
        checkpoint_sqlite_path=tmp_path / "checkpoints.sqlite",
    )


def begin(
    session,
    settings,
    request_text="I need write access to the payments repository.",
    employee_id="EMP001",
):
    ticket = create_ticket(
        session, TicketCreate(employee_id=employee_id, request_text=request_text)
    )
    return submit_run(session, ticket.id, settings)


def permission(session, employee_id="EMP001"):
    return session.scalar(
        select(RepositoryPermission.permission)
        .join(Repository)
        .where(RepositoryPermission.employee_id == employee_id, Repository.name == "payments")
    )


@pytest.mark.parametrize("decision", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_payments_pauses_and_resumes_from_recorded_decision(
    engine, db_session, flow_settings, decision
):
    run = begin(db_session, flow_settings)
    worker = AgentWorker(sessionmaker(bind=engine), flow_settings)
    assert worker.run_once() == run.id
    db_session.expire_all()
    paused = db_session.get(AgentRun, run.id)
    assert paused.status == AgentRunStatus.WAITING_FOR_APPROVAL, paused.state
    assert permission(db_session) == Permission.READ
    assert worker.run_once() is None
    approval = db_session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run.id))
    assert approval.status == ApprovalStatus.PENDING
    reviewer = db_session.get(User, approval.approver_id)
    decide_approval(db_session, approval.id, reviewer, decision, "Reviewed business need.")
    assert AgentWorker(sessionmaker(bind=engine), flow_settings).run_once() == run.id
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.status == AgentRunStatus.COMPLETED, done.state
    ticket = db_session.get(Ticket, run.ticket_id)
    tools = db_session.scalars(
        select(ToolExecution.tool_name).where(ToolExecution.run_id == run.id)
    ).all()
    if decision == ApprovalStatus.APPROVED:
        assert permission(db_session) == Permission.WRITE
        assert ticket.status == TicketStatus.RESOLVED
        assert done.state["verification_result"]["sufficient"]
        assert done.state["outcome"] == "granted"
        assert "close_ticket" in tools
        assert tools.count("grant_repository_permission") == 1
        assert "Repository Access Policy" in ticket.final_response
    else:
        assert permission(db_session) == Permission.READ
        assert ticket.status == TicketStatus.ESCALATED
        assert done.state["outcome"] == "rejected"
        assert "grant_repository_permission" not in tools
        assert "close_ticket" not in tools
    assert worker.run_once() is None


@pytest.mark.parametrize(
    "request_text,expected",
    [("Read access to payments", "already_sufficient"), ("Admin access to payments", "escalated")],
)
def test_existing_and_admin_routes(engine, db_session, flow_settings, request_text, expected):
    run = begin(db_session, flow_settings, request_text)
    AgentWorker(sessionmaker(bind=engine), flow_settings).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.status == AgentRunStatus.COMPLETED, done.state
    assert done.state["outcome"] == expected
    assert permission(db_session) == Permission.READ
    assert (
        db_session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run.id)) is None
    )
