"""Run fixed evaluations against disposable databases, never against demo permissions."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.db.base import Base
from app.db.seed import seed_database
from app.db.session import build_engine
from app.evaluation.contracts import EvaluationTrace
from app.evaluation.scenarios import Fault, Scenario
from app.evaluation.trace import collect_trace
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
    User,
)
from app.providers import (
    Classification,
    DemoProvider,
    ProviderError,
    RepositoryArguments,
    RepositoryToolCall,
)
from app.rag.contracts import PolicySearchResult
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.schemas.tickets import TicketCreate
from app.services.approvals import decide_approval
from app.services.runs import submit_run
from app.services.tickets import create_ticket
from app.tools.domain import ToolError, get_repository_permission
from app.tools.schemas import PermissionLookup


class FixtureProvider(DemoProvider):
    def __init__(self, fault: Fault):
        self.fault = fault

    def classify(self, request_text: str) -> Classification:
        if self.fault == Fault.LLM_TIMEOUT:
            raise ProviderError("provider_timeout", "Evaluation fixture: provider timed out.")
        if self.fault == Fault.MALFORMED_OUTPUT:
            # Exercise Pydantic rejection rather than replacing the workflow's error path.
            return Classification.model_validate({"intent": "not-a-supported-intent"})
        return super().classify(request_text)

    def select_repository_tool(self, repository_name: str) -> RepositoryToolCall:
        if self.fault == Fault.TOOL_SCOPE:
            return RepositoryToolCall(
                tool_name="find_repository",
                arguments=RepositoryArguments(repository_name="finance-reporting"),
            )
        return super().select_repository_tool(repository_name)


def tool_faults(fault: Fault) -> dict[str, Callable]:
    def failed(_session: Session, _args: BaseModel) -> BaseModel:
        code = "employee_not_found" if fault == Fault.EMPLOYEE_LOOKUP else "fixture_tool_failure"
        raise ToolError(code, "Evaluation fixture: the selected service is unavailable.")

    mapping = {
        Fault.GRANT_FAILURE: "grant_repository_permission",
        Fault.POLICY_FAILURE: "search_policies",
        Fault.EMPLOYEE_LOOKUP: "get_employee",
    }
    if fault in mapping:
        return {mapping[fault]: failed}
    if fault == Fault.EMPTY_POLICY:

        def empty_policy(_session: Session, _args: BaseModel) -> PolicySearchResult:
            provider = LocalHashEmbeddingProvider()
            return PolicySearchResult(
                evidence=[],
                embedding_provider=provider.provider_name,
                embedding_model=provider.model_name,
                dimensions=provider.dimensions,
            )

        return {"search_policies": empty_policy}
    if fault == Fault.VERIFICATION_FAILURE:
        reads = 0

        def readback(session: Session, args: PermissionLookup):
            nonlocal reads
            reads += 1
            if reads == 2:
                raise ToolError("fixture_readback_failure", "Evaluation fixture: readback failed.")
            return get_repository_permission(session, args)

        return {"get_repository_permission": readback}
    return {}


def permission_record(session: Session, scenario: Scenario) -> RepositoryPermission | None:
    repository = session.scalar(
        select(Repository).where(Repository.name == scenario.repository_name)
    )
    if repository is None:
        return None
    return session.scalar(
        select(RepositoryPermission).where(
            RepositoryPermission.employee_id == scenario.employee_id,
            RepositoryPermission.repository_id == repository.id,
        )
    )


def permission_value(session: Session, scenario: Scenario) -> str | None:
    if (
        scenario.repository_name is None
        or session.scalar(select(Repository.id).where(Repository.name == scenario.repository_name))
        is None
    ):
        return None
    row = permission_record(session, scenario)
    return row.permission.value if row else Permission.NONE.value


def prepare_fixture(session: Session, scenario: Scenario) -> None:
    if scenario.initial_permission is not None:
        row = permission_record(session, scenario)
        if row is None:
            repository = session.scalar(
                select(Repository).where(Repository.name == scenario.repository_name)
            )
            if repository is None:
                raise ValueError("An initial permission fixture requires a catalog repository.")
            row = RepositoryPermission(
                employee_id=scenario.employee_id, repository_id=repository.id
            )
            session.add(row)
        row.permission = Permission(scenario.initial_permission)
    if scenario.fault == Fault.INACTIVE_EMPLOYEE:
        session.get(Employee, scenario.employee_id).is_active = False
    elif scenario.fault == Fault.INACTIVE_MANAGER:
        employee = session.get(Employee, scenario.employee_id)
        session.get(Employee, employee.manager_id).is_active = False
    session.commit()


def after_decision_fixture(session: Session, scenario: Scenario, approval: ApprovalRequest) -> None:
    if scenario.fault == Fault.INACTIVE_REVIEWER:
        reviewer = session.get(User, approval.decided_by_id)
        session.get(Employee, reviewer.employee_id).is_active = False
    elif scenario.fault == Fault.MISSING_APPROVAL:
        session.delete(approval)
    elif scenario.fault == Fault.CHANGED_POLICY:
        document = session.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.slug == "repository-access-policy")
        )
        document.content += "\nEvaluation fixture: policy changed during review.\n"
    session.commit()


def run_scenario(scenario: Scenario, settings: Settings) -> EvaluationTrace:
    """Execute one real graph including durable pause/resume and independently read effects."""
    with TemporaryDirectory(prefix="resolveai-evaluation-") as directory:
        workspace = Path(directory)
        configured = settings.model_copy(
            update={
                "app_env": "test",
                "database_url": f"sqlite+pysqlite:///{(workspace / 'domain.sqlite').as_posix()}",
                "checkpoint_sqlite_path": workspace / "checkpoints.sqlite",
                "llm_provider": "demo",
                "embedding_provider": "local_hash",
                "openai_api_key": None,
                "agent_worker_enabled": False,
                "rag_top_k": 8,
                "rag_min_score": 0.1,
            }
        )
        engine = build_engine(configured.database_url)
        try:
            Base.metadata.create_all(engine)
            sessions = sessionmaker(bind=engine, expire_on_commit=False)
            embeddings = LocalHashEmbeddingProvider()
            with sessions() as session:
                seed_database(session, configured.policy_directory, embeddings)
                ticket = create_ticket(
                    session,
                    TicketCreate(
                        employee_id=scenario.employee_id,
                        request_text=scenario.request_text,
                    ),
                )
                prepare_fixture(session, scenario)
                initial = permission_value(session, scenario)
                run_id = submit_run(session, ticket.id, configured).id
            worker = AgentWorker(
                sessions,
                configured,
                provider=FixtureProvider(scenario.fault),
                embedding_provider=embeddings,
                tool_overrides=tool_faults(scenario.fault),
                evaluate_completed=False,
            )
            worker.run_once()
            with sessions() as session:
                run = session.get(AgentRun, run_id)
                should_resume = False
                if run.status == AgentRunStatus.WAITING_FOR_APPROVAL and scenario.approval_decision:
                    approval = session.scalar(
                        select(ApprovalRequest).where(ApprovalRequest.run_id == run_id)
                    )
                    if approval is None:
                        raise RuntimeError("A paused scenario has no approval request.")
                    actor = session.get(User, approval.approver_id)
                    approval = decide_approval(
                        session,
                        approval.id,
                        actor,
                        ApprovalStatus(scenario.approval_decision),
                        f"Recorded by deterministic scenario {scenario.id}.",
                    )
                    after_decision_fixture(session, scenario, approval)
                    should_resume = True
            if should_resume:
                # A fresh worker consumes durable state, not the preceding graph instance.
                AgentWorker(
                    sessions,
                    configured,
                    provider=FixtureProvider(scenario.fault),
                    embedding_provider=embeddings,
                    tool_overrides=worker.tool_overrides,
                    evaluate_completed=False,
                ).run_once()
            with sessions() as session:
                return collect_trace(
                    session,
                    run_id,
                    initial_permission=initial,
                    final_permission=permission_value(session, scenario),
                )
        finally:
            engine.dispose()
