from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utc_now
from app.models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    ConversationMessage,
    ConversationRole,
    Permission,
    Repository,
    RepositoryPermission,
    Ticket,
    TicketStatus,
    ToolExecution,
    ToolExecutionStatus,
)
from app.tools import ToolContextError, ToolRunner
from app.tools.domain import ToolError, get_employee, get_repository_permission
from app.tools.runner import TOOL_REGISTRY
from app.tools.schemas import EmployeeLookup, EscalationInput, PermissionLookup, ToolResult


@dataclass(frozen=True)
class ToolHarness:
    runner: ToolRunner
    sessions: sessionmaker[Session]
    ticket_id: UUID
    run_id: UUID
    step_id: UUID

    def execute(self, tool_name: str, arguments: dict, key: str = "test-tool") -> ToolResult:
        return self.runner.execute(
            run_id=self.run_id,
            step_id=self.step_id,
            tool_name=tool_name,
            arguments=arguments,
            idempotency_key=key,
        )


@pytest.fixture
def tool_harness(engine: Engine, db_session: Session) -> ToolHarness:
    ticket = Ticket(
        employee_id="EMP001",
        request_text="I need write access to the payments repository.",
        status=TicketStatus.PROCESSING,
    )
    db_session.add(ticket)
    db_session.flush()
    run = AgentRun(ticket_id=ticket.id, status=AgentRunStatus.RUNNING)
    db_session.add(run)
    db_session.flush()
    step = AgentStep(
        run_id=run.id,
        sequence=0,
        node="IDENTIFY_EMPLOYEE",
        status=AgentStepStatus.RUNNING,
        summary="Looking up the ticket employee.",
    )
    db_session.add(step)
    db_session.commit()
    sessions = sessionmaker(bind=engine)
    return ToolHarness(ToolRunner(sessions), sessions, ticket.id, run.id, step.id)


def test_employee_lookup_returns_catalog_identity_and_persists_trace(
    tool_harness: ToolHarness,
) -> None:
    result = tool_harness.execute("get_employee", {"employee_id": "EMP001"})
    assert result.succeeded is True
    assert result.output["name"] == "Chetan Aditya"
    assert result.output["manager_id"] == "EMP002"
    with tool_harness.sessions() as session:
        trace = session.get(ToolExecution, result.execution_id)
        assert trace.run_id == tool_harness.run_id
        assert trace.step_id == tool_harness.step_id
        assert trace.status == ToolExecutionStatus.SUCCEEDED
        assert trace.arguments == {"employee_id": "EMP001"}
        assert trace.result["tool_result"]["succeeded"] is True
        assert trace.latency_ms >= 0
        assert trace.completed_at is not None


def test_repository_lookup_uses_exact_case_insensitive_catalog_match(
    tool_harness: ToolHarness,
) -> None:
    result = tool_harness.execute("find_repository", {"repository_name": " PAYMENTS "})
    assert result.succeeded is True
    assert result.output["name"] == "payments"
    assert result.output["owning_department"] == "Engineering"


@pytest.mark.parametrize("repository_name", ["payment", "never-existed", "payments OR 1=1"])
def test_repository_lookup_never_invents_a_resource(
    tool_harness: ToolHarness, repository_name: str
) -> None:
    result = tool_harness.execute("find_repository", {"repository_name": repository_name})
    assert result.succeeded is False
    assert result.error_code == "repository_not_found"
    assert result.output == {}


@pytest.mark.parametrize(
    ("repository_name", "expected"),
    [("payments", Permission.READ), ("finance-reporting", Permission.NONE)],
)
def test_permission_lookup_distinguishes_existing_and_absent_records_without_mutation(
    tool_harness: ToolHarness, repository_name: str, expected: Permission
) -> None:
    with tool_harness.sessions() as session:
        repository = session.scalar(select(Repository).where(Repository.name == repository_name))
        repository_id = repository.id
        count_before = session.scalar(select(func.count()).select_from(RepositoryPermission))
    result = tool_harness.execute(
        "get_repository_permission",
        {"employee_id": "EMP001", "repository_id": str(repository_id)},
    )
    assert result.succeeded is True
    assert result.output["permission"] == expected
    with tool_harness.sessions() as session:
        assert (
            session.scalar(select(func.count()).select_from(RepositoryPermission)) == count_before
        )


def test_permission_lookup_rejects_unknown_repository(tool_harness: ToolHarness) -> None:
    result = tool_harness.execute(
        "get_repository_permission", {"employee_id": "EMP001", "repository_id": str(uuid4())}
    )
    assert result.succeeded is False
    assert result.error_code == "repository_not_found"


def test_employee_domain_lookup_rejects_unknown_identity(db_session: Session) -> None:
    with pytest.raises(ToolError) as error:
        get_employee(db_session, EmployeeLookup(employee_id="UNKNOWN"))
    assert error.value.code == "employee_not_found"


def test_permission_domain_lookup_rejects_unknown_identity(db_session: Session) -> None:
    repository = db_session.scalar(select(Repository).where(Repository.name == "payments"))
    with pytest.raises(ToolError) as error:
        get_repository_permission(
            db_session, PermissionLookup(employee_id="UNKNOWN", repository_id=repository.id)
        )
    assert error.value.code == "employee_not_found"


@pytest.mark.parametrize("employee_id", ["EMP003", "UNKNOWN"])
def test_employee_tool_cannot_change_ticket_identity(
    tool_harness: ToolHarness, employee_id: str
) -> None:
    result = tool_harness.execute("get_employee", {"employee_id": employee_id})
    assert result.succeeded is False
    assert result.error_code == "scope_mismatch"


def test_escalation_cannot_modify_another_ticket(tool_harness: ToolHarness) -> None:
    with tool_harness.sessions() as session:
        other = Ticket(employee_id="EMP004", request_text="read access to finance-reporting")
        session.add(other)
        session.commit()
        other_id = other.id
    result = tool_harness.execute(
        "escalate_ticket", {"ticket_id": str(other_id), "reason": "Untrusted cross-ticket action"}
    )
    assert result.succeeded is False
    assert result.error_code == "scope_mismatch"
    with tool_harness.sessions() as session:
        assert session.get(Ticket, other_id).status == TicketStatus.OPEN
        assert session.get(Ticket, tool_harness.ticket_id).status == TicketStatus.PROCESSING
        assert session.scalar(select(func.count()).select_from(ConversationMessage)) == 0


@pytest.mark.parametrize(
    ("tool_name", "arguments", "error_code"),
    [
        ("get_employee", {}, "invalid_arguments"),
        ("get_employee", {"employee_id": ""}, "invalid_arguments"),
        ("get_employee", {"employee_id": "EMP001", "secret": "do-not-store"}, "invalid_arguments"),
        ("grant_repository_permission", {"permission": "admin"}, "execution_not_allowed"),
        ("run_shell_command", {"command": "do-not-store"}, "tool_not_allowed"),
    ],
)
def test_invalid_and_unavailable_tools_are_failed_and_audited(
    tool_harness: ToolHarness, tool_name: str, arguments: dict, error_code: str
) -> None:
    result = tool_harness.execute(tool_name, arguments)
    assert result.succeeded is False
    assert result.error_code == error_code
    with tool_harness.sessions() as session:
        trace = session.get(ToolExecution, result.execution_id)
        assert trace.status == ToolExecutionStatus.FAILED
        assert trace.error == error_code
        assert trace.completed_at is not None
        assert "do-not-store" not in str(trace.arguments)


def test_escalation_commits_ticket_message_and_execution_together(
    tool_harness: ToolHarness,
) -> None:
    reason = "Admin access requires Security review. No permission was changed."
    result = tool_harness.execute(
        "escalate_ticket", {"ticket_id": str(tool_harness.ticket_id), "reason": reason}
    )
    assert result.succeeded is True
    assert result.output["status"] == "ESCALATED"
    with tool_harness.sessions() as session:
        ticket = session.get(Ticket, tool_harness.ticket_id)
        assert ticket.status == TicketStatus.ESCALATED
        assert ticket.final_response == reason
        assert ticket.resolved_at is None
        message = session.scalar(
            select(ConversationMessage).where(ConversationMessage.ticket_id == ticket.id)
        )
        assert message.role == ConversationRole.ASSISTANT
        assert message.content == reason
        assert (
            session.get(ToolExecution, result.execution_id).status == ToolExecutionStatus.SUCCEEDED
        )


def test_escalation_cannot_reopen_resolved_ticket(tool_harness: ToolHarness) -> None:
    with tool_harness.sessions() as session:
        ticket = session.get(Ticket, tool_harness.ticket_id)
        ticket.status = TicketStatus.RESOLVED
        ticket.resolved_at = utc_now()
        session.commit()
    result = tool_harness.execute(
        "escalate_ticket", {"ticket_id": str(tool_harness.ticket_id), "reason": "Retry escalation"}
    )
    assert result.succeeded is False
    assert result.error_code == "ticket_locked"
    with tool_harness.sessions() as session:
        assert session.get(Ticket, tool_harness.ticket_id).status == TicketStatus.RESOLVED


def test_replayed_escalation_returns_original_result_without_duplicate_message(
    tool_harness: ToolHarness,
) -> None:
    arguments = {"ticket_id": str(tool_harness.ticket_id), "reason": "Security review required."}
    first = tool_harness.execute("escalate_ticket", arguments)
    second = tool_harness.execute("escalate_ticket", arguments)
    assert first.succeeded is True
    assert second == first
    with tool_harness.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 1
        assert session.scalar(select(func.count()).select_from(ConversationMessage)) == 1


def test_idempotency_key_cannot_be_reused_with_different_arguments(
    tool_harness: ToolHarness,
) -> None:
    first = tool_harness.execute("find_repository", {"repository_name": "payments"})
    second = tool_harness.execute("find_repository", {"repository_name": "security-audit"})
    assert first.succeeded is True
    assert second.succeeded is False
    assert second.error_code == "idempotency_conflict"
    assert second.execution_id != first.execution_id
    with tool_harness.sessions() as session:
        assert (
            session.get(ToolExecution, first.execution_id).status == ToolExecutionStatus.SUCCEEDED
        )
        assert session.get(ToolExecution, second.execution_id).status == ToolExecutionStatus.FAILED


def test_interrupted_tool_record_is_retried_with_original_execution_id(
    tool_harness: ToolHarness,
) -> None:
    arguments = {"employee_id": "EMP001"}
    first = tool_harness.execute("get_employee", arguments)
    with tool_harness.sessions() as session:
        trace = session.get(ToolExecution, first.execution_id)
        trace.status = ToolExecutionStatus.RUNNING
        trace.completed_at = None
        trace.result = {"fingerprint": trace.result["fingerprint"]}
        session.commit()
    replay = tool_harness.execute("get_employee", arguments)
    assert replay.succeeded is True
    assert replay.execution_id == first.execution_id
    with tool_harness.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 1
        assert (
            session.get(ToolExecution, replay.execution_id).status == ToolExecutionStatus.SUCCEEDED
        )


def test_tool_failure_rolls_back_domain_mutation_and_records_failure(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_escalation(session: Session, args: EscalationInput) -> None:
        session.get(Ticket, args.ticket_id).status = TicketStatus.ESCALATED
        session.flush()
        raise RuntimeError("sensitive implementation error")

    monkeypatch.setitem(TOOL_REGISTRY, "escalate_ticket", (EscalationInput, failing_escalation))
    result = tool_harness.execute(
        "escalate_ticket", {"ticket_id": str(tool_harness.ticket_id), "reason": "Review required"}
    )
    assert result.succeeded is False
    assert result.error_code == "tool_failed"
    assert "sensitive" not in result.summary
    with tool_harness.sessions() as session:
        assert session.get(Ticket, tool_harness.ticket_id).status == TicketStatus.PROCESSING
        assert session.get(ToolExecution, result.execution_id).status == ToolExecutionStatus.FAILED


def test_database_failure_leaves_tool_retryable_without_claiming_success(
    tool_harness: ToolHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def database_failure(session: Session, args: EscalationInput) -> None:
        session.get(Ticket, args.ticket_id).status = TicketStatus.ESCALATED
        session.flush()
        raise SQLAlchemyError("Database temporarily unavailable")

    monkeypatch.setitem(TOOL_REGISTRY, "escalate_ticket", (EscalationInput, database_failure))
    with pytest.raises(SQLAlchemyError):
        tool_harness.execute(
            "escalate_ticket",
            {"ticket_id": str(tool_harness.ticket_id), "reason": "Review required"},
        )
    with tool_harness.sessions() as session:
        assert session.get(Ticket, tool_harness.ticket_id).status == TicketStatus.PROCESSING
        trace = session.scalar(select(ToolExecution))
        assert trace.status == ToolExecutionStatus.RUNNING
        assert "tool_result" not in trace.result


@pytest.mark.parametrize("invalid_part", ["run", "step"])
def test_tool_requires_existing_matching_execution_context(
    tool_harness: ToolHarness, invalid_part: str
) -> None:
    with pytest.raises(ToolContextError):
        tool_harness.runner.execute(
            run_id=uuid4() if invalid_part == "run" else tool_harness.run_id,
            step_id=uuid4() if invalid_part == "step" else tool_harness.step_id,
            tool_name="get_employee",
            arguments={"employee_id": "EMP001"},
            idempotency_key="missing-context",
        )
    with tool_harness.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 0


def test_tool_rejects_step_belonging_to_another_run(tool_harness: ToolHarness) -> None:
    with tool_harness.sessions() as session:
        other_ticket = Ticket(employee_id="EMP004", request_text="read access to finance-reporting")
        session.add(other_ticket)
        session.flush()
        other_run = AgentRun(ticket_id=other_ticket.id, status=AgentRunStatus.RUNNING)
        session.add(other_run)
        session.flush()
        other_step = AgentStep(
            run_id=other_run.id,
            sequence=0,
            node="IDENTIFY_EMPLOYEE",
            status=AgentStepStatus.RUNNING,
            summary="Employee lookup for another ticket.",
        )
        session.add(other_step)
        session.commit()
        other_step_id = other_step.id
    with pytest.raises(ToolContextError):
        tool_harness.runner.execute(
            run_id=tool_harness.run_id,
            step_id=other_step_id,
            tool_name="get_employee",
            arguments={"employee_id": "EMP001"},
            idempotency_key="mismatched-step",
        )
    with tool_harness.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ToolExecution)) == 0
