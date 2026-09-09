from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest
from langgraph._internal import _serde as graph_serde
from langgraph.checkpoint.serde import _msgpack
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.checkpoints import checkpoint_store
from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.db.base import utc_now
from app.models import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
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
from app.services.tickets import create_ticket


@dataclass
class UnapprovedCheckpointObject:
    value: int

    def __post_init__(self):
        raise AssertionError("A checkpoint must not construct this application type.")


@pytest.fixture
def strict_settings(db_session, tmp_path, monkeypatch):
    # The pinned packages cache this environment flag at import time. Patch both caches
    # to exercise strict graph compilation even when the full suite imported them first.
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    monkeypatch.setattr(_msgpack, "STRICT_MSGPACK_ENABLED", True)
    monkeypatch.setattr(graph_serde, "STRICT_MSGPACK_ENABLED", True)
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    db_session.commit()
    return Settings(
        database_url="sqlite+pysqlite://",
        checkpoint_sqlite_path=tmp_path / "checkpoints.sqlite",
        llm_provider="demo",
        embedding_provider="local_hash",
        agent_worker_enabled=False,
    )


@pytest.mark.parametrize("strict_enabled", [True, False])
def test_checkpoint_constructor_allowlist_is_exact(strict_settings, monkeypatch, strict_enabled):
    monkeypatch.setattr(_msgpack, "STRICT_MSGPACK_ENABLED", strict_enabled)
    monkeypatch.setattr(graph_serde, "STRICT_MSGPACK_ENABLED", strict_enabled)
    with checkpoint_store(strict_settings) as saver:
        value = {"permission": Permission.WRITE, "id": uuid4()}
        assert saver.serde.loads_typed(saver.serde.dumps_typed(value)) == value
        assert isinstance(
            saver.serde.loads_typed(saver.serde.dumps_typed(Permission.WRITE)), Permission
        )
        # A sibling enum from the very same module must not be implicitly trusted.
        sibling = saver.serde.loads_typed(saver.serde.dumps_typed(UserRole.ADMIN))
        assert type(sibling) is str
        # Bypass initialization to construct only the test payload; loading must not
        # execute __post_init__. The library returns blocked constructors as plain data.
        payload = object.__new__(UnapprovedCheckpointObject)
        payload.value = 7
        assert saver.serde.loads_typed(saver.serde.dumps_typed(payload)) == {"value": 7}


def test_checkpoint_does_not_accept_pickle(strict_settings):
    with checkpoint_store(strict_settings) as saver:
        with pytest.raises(NotImplementedError, match="Unknown serialization type: pickle"):
            saver.serde.loads_typed(("pickle", b"untrusted checkpoint bytes"))


def paused_payment(engine, session, settings):
    ticket = create_ticket(
        session,
        TicketCreate(
            employee_id="EMP001", request_text="I need write access to the payments repository."
        ),
    )
    run = runs.submit_run(session, ticket.id, settings)
    AgentWorker(sessionmaker(bind=engine), settings).run_once()
    session.expire_all()
    run = session.get(AgentRun, run.id)
    assert run.status == AgentRunStatus.WAITING_FOR_APPROVAL, run.state
    approval = session.scalar(select(ApprovalRequest).where(ApprovalRequest.run_id == run.id))
    return run, approval


@pytest.mark.parametrize("decision", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_strict_checkpoint_reopens_and_resumes_recorded_decision(
    engine, db_session, strict_settings, decision, caplog
):
    run, approval = paused_payment(engine, db_session, strict_settings)
    # The first worker's SQLite connection is closed; load its durable snapshot afresh.
    with checkpoint_store(strict_settings) as saver:
        snapshot = saver.get_tuple({"configurable": {"thread_id": run.graph_thread_id}})
        assert snapshot is not None
        values = snapshot.checkpoint["channel_values"]
        assert values["requested_permission"] is Permission.WRITE
        assert values["approval_id"] == str(approval.id)
    decide_approval(
        db_session,
        approval.id,
        db_session.get(User, approval.approver_id),
        decision,
        "Reviewed repository write access.",
    )
    AgentWorker(sessionmaker(bind=engine), strict_settings).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).status == AgentRunStatus.COMPLETED
    granted = decision == ApprovalStatus.APPROVED
    assert db_session.get(Ticket, run.ticket_id).status == (
        TicketStatus.RESOLVED if granted else TicketStatus.ESCALATED
    )
    observed = db_session.scalar(
        select(RepositoryPermission.permission)
        .join(Repository)
        .where(RepositoryPermission.employee_id == "EMP001", Repository.name == "payments")
    )
    assert observed == (Permission.WRITE if granted else Permission.READ)
    assert not any(
        "Deserializing unregistered type" in record.message
        or "Blocked deserialization" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize("crash_node", ["EXECUTE_ACTION", "RESOLVE"])
def test_strict_checkpoint_recovery_after_tool_commit(
    engine, db_session, strict_settings, monkeypatch, crash_node
):
    run, approval = paused_payment(engine, db_session, strict_settings)
    decide_approval(
        db_session,
        approval.id,
        db_session.get(User, approval.approver_id),
        ApprovalStatus.APPROVED,
        "Approved.",
    )
    original = runs.finish_step

    def crash_after_tool(*args, **kwargs):
        if args[4].current_node == crash_node:
            raise RuntimeError("Simulated termination after the domain tool committed.")
        return original(*args, **kwargs)

    monkeypatch.setattr(runs, "finish_step", crash_after_tool)
    AgentWorker(sessionmaker(bind=engine), strict_settings).run_once()
    db_session.expire_all()
    recovering = db_session.get(AgentRun, run.id)
    assert recovering.status == AgentRunStatus.RUNNING
    recovering.lease_expires_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    monkeypatch.setattr(runs, "finish_step", original)
    AgentWorker(sessionmaker(bind=engine), strict_settings).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).state["outcome"] == "granted"
    tools = list(
        db_session.scalars(select(ToolExecution.tool_name).where(ToolExecution.run_id == run.id))
    )
    assert tools.count("grant_repository_permission") == 1
    assert tools.count("close_ticket") == 1
