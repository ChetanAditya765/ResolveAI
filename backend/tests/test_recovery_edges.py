import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents import checkpoints
from app.core.config import Settings
from app.models import AgentRun, Ticket, TicketStatus, ToolExecution, ToolExecutionStatus
from app.schemas.tickets import TicketCreate
from app.services import runs
from app.services.tickets import create_ticket
from app.tools import ToolRunner
from app.tools.runner import TOOL_REGISTRY
from app.tools.schemas import EmployeeLookup, ToolResult


@pytest.fixture
def recovery_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite+pysqlite://",
        app_env="test",
        llm_provider="demo",
        checkpoint_sqlite_path=tmp_path / "checkpoints.sqlite",
        agent_worker_enabled=False,
    )


@dataclass(frozen=True)
class LeasedTool:
    runner: ToolRunner
    run_id: UUID
    step_id: UUID
    ticket_id: UUID

    def execute(self) -> ToolResult:
        return self.runner.execute(
            run_id=self.run_id,
            step_id=self.step_id,
            tool_name="get_employee",
            arguments={"employee_id": "EMP001"},
            idempotency_key="recovery-employee-lookup",
            lease_owner="original-worker",
        )


@pytest.fixture
def leased_tool(engine: Engine, db_session: Session, recovery_settings: Settings) -> LeasedTool:
    ticket = create_ticket(
        db_session,
        TicketCreate(employee_id="EMP001", request_text="I need write access to payments."),
    )
    run = runs.submit_run(db_session, ticket.id, recovery_settings)
    runs.claim_run(db_session, "original-worker", recovery_settings.agent_lease_seconds)
    step = runs.begin_step(
        db_session,
        run.id,
        "original-worker",
        2,
        "IDENTIFY_EMPLOYEE",
        recovery_settings.agent_lease_seconds,
    )
    return LeasedTool(ToolRunner(sessionmaker(bind=engine)), run.id, step.id, ticket.id)


@pytest.mark.parametrize("failure_point", ["none", "graph", "setup"])
def test_sqlite_checkpoint_connection_always_closes(
    recovery_settings: Settings, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    def failed_setup(_saver) -> None:
        raise RuntimeError("Checkpoint initialization failed.")

    monkeypatch.setattr(checkpoints.sqlite3, "connect", tracked_connect)
    if failure_point == "setup":
        monkeypatch.setattr(checkpoints.SqliteSaver, "setup", failed_setup)

    if failure_point == "none":
        with checkpoints.checkpoint_store(recovery_settings):
            pass
    else:
        with pytest.raises(RuntimeError):
            with checkpoints.checkpoint_store(recovery_settings):
                raise RuntimeError("Graph interrupted.")

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_lease_loss_between_tool_audit_and_handler_leaves_retryable_execution(
    leased_tool: LeasedTool, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_owned_run = runs.owned_run
    ownership_checks = 0

    def replace_worker_before_handler(session: Session, run_id: UUID, owner: str):
        nonlocal ownership_checks
        ownership_checks += 1
        if ownership_checks == 2:
            session.get(AgentRun, run_id).lease_owner = "replacement-worker"
            session.commit()
        return original_owned_run(session, run_id, owner)

    monkeypatch.setattr(runs, "owned_run", replace_worker_before_handler)
    with pytest.raises(runs.LeaseLost):
        leased_tool.execute()

    with Session(engine) as session:
        trace = session.scalar(select(ToolExecution))
        assert trace.status == ToolExecutionStatus.RUNNING
        assert "tool_result" not in trace.result
        assert session.get(Ticket, leased_tool.ticket_id).status == TicketStatus.PROCESSING
        assert session.get(AgentRun, leased_tool.run_id).lease_owner == "replacement-worker"


def test_stale_worker_cannot_overwrite_replacement_result_after_rollback(
    leased_tool: LeasedTool, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    replacement_results: list[dict] = []

    class TakeoverOnRollbackSession(Session):
        takeover_done = False

        def rollback(self) -> None:
            super().rollback()
            if self.takeover_done:
                return
            self.takeover_done = True
            with Session(engine) as replacement:
                replacement.get(AgentRun, leased_tool.run_id).lease_owner = "replacement-worker"
                trace = replacement.scalar(select(ToolExecution))
                result = ToolResult(
                    execution_id=trace.id,
                    tool_name="get_employee",
                    succeeded=True,
                    output={"id": "EMP001"},
                    summary="Replacement worker completed the lookup.",
                )
                trace.status = ToolExecutionStatus.SUCCEEDED
                trace.result = {
                    **trace.result,
                    "tool_result": result.model_dump(mode="json"),
                }
                replacement_results.append(trace.result)
                replacement.commit()

    def failing_handler(_session: Session, _args: EmployeeLookup) -> None:
        raise RuntimeError("The original worker failed before producing a result.")

    monkeypatch.setitem(TOOL_REGISTRY, "get_employee", (EmployeeLookup, failing_handler))
    runner = ToolRunner(sessionmaker(bind=engine, class_=TakeoverOnRollbackSession))
    tool = LeasedTool(runner, leased_tool.run_id, leased_tool.step_id, leased_tool.ticket_id)
    with pytest.raises(runs.LeaseLost):
        tool.execute()

    with Session(engine) as session:
        trace = session.scalar(select(ToolExecution))
        assert trace.status == ToolExecutionStatus.SUCCEEDED
        assert trace.result == replacement_results[0]
        assert trace.error is None
        assert session.get(AgentRun, leased_tool.run_id).lease_owner == "replacement-worker"
