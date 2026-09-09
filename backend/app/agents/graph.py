from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.agents.state import AgentState, DecisionSummary, WorkflowNode
from app.core.config import Settings
from app.models import AgentStepStatus, Permission
from app.providers import AgentProvider, Classification, ProviderError, RepositoryToolCall
from app.rag.contracts import EmbeddingProvider, PolicySearchResult
from app.services import runs
from app.tools import EmployeeInfo, PermissionInfo, RepositoryInfo, ToolContextError, ToolRunner


@dataclass
class NodeResult:
    delta: dict
    summary: str


class AccessWorkflow:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        provider: AgentProvider,
        settings: Settings,
        run_id: UUID,
        owner: str,
        embedding_provider: EmbeddingProvider | None = None,
        tool_overrides: dict[str, Callable] | None = None,
    ) -> None:
        self.sessions, self.provider, self.settings = sessions, provider, settings
        self.run_id, self.owner = run_id, owner
        self.tools = ToolRunner(sessions, settings, embedding_provider, tool_overrides)

    def audited(self, node: WorkflowNode, sequence: int, operation: Callable) -> Callable:
        def execute(state: AgentState) -> dict:
            with self.sessions() as session:
                step = runs.begin_step(
                    session,
                    self.run_id,
                    self.owner,
                    sequence,
                    node.value,
                    self.settings.agent_lease_seconds,
                )
                if step.status != AgentStepStatus.RUNNING and "state_delta" in step.details:
                    return step.details["state_delta"]
                step_id = step.id
            started = perf_counter()
            try:
                result = operation(state, step_id)
            except (ProviderError, ValidationError, ToolContextError) as exc:
                code = exc.code if isinstance(exc, ProviderError) else "invalid_agent_output"
                result = NodeResult(
                    {
                        "error_code": code,
                        "final_response": "The request could not be processed "
                        "reliably and requires manual review. "
                        + (
                            "Access changes could not be verified."
                            if state.execution_result
                            else "No access was changed."
                        ),
                    },
                    "Execution stopped safely; manual review is required.",
                )
            delta = {**result.delta, "current_node": node.value}
            updated = AgentState.model_validate({**state.model_dump(mode="json"), **delta})
            with self.sessions() as session:
                runs.finish_step(
                    session,
                    self.run_id,
                    self.owner,
                    step_id,
                    updated,
                    delta,
                    result.summary,
                    round((perf_counter() - started) * 1000),
                    self.settings.agent_lease_seconds,
                )
            return delta

        return execute

    def tool(self, step_id: UUID, name: str, arguments: dict):
        return self.tools.execute(
            run_id=self.run_id,
            step_id=step_id,
            tool_name=name,
            arguments=arguments,
            idempotency_key=f"{self.run_id}:{name}",
            lease_owner=self.owner,
        )

    def receive(self, state: AgentState, _step: UUID) -> NodeResult:
        return NodeResult({}, "Employee access request received.")

    def employee(self, state: AgentState, step: UUID) -> NodeResult:
        result = self.tool(step, "get_employee", {"employee_id": state.employee_id})
        if not result.succeeded:
            return NodeResult({"error_code": result.error_code}, "Employee lookup failed.")
        employee = EmployeeInfo.model_validate(result.output)
        if not employee.is_active:
            return NodeResult(
                {"employee": employee.model_dump(mode="json"), "error_code": "employee_inactive"},
                "Employee is inactive; access changes are prohibited.",
            )
        return NodeResult(
            {"employee": employee.model_dump(mode="json")},
            f"{employee.name} identified in {employee.department}.",
        )

    def classify(self, state: AgentState, _step: UUID) -> NodeResult:
        proposed = self.provider.classify(state.request_text)
        classification = Classification.model_validate(proposed.model_dump(mode="json"))
        delta = {
            "intent": classification.intent,
            "repository_name": classification.repository_name,
            "requested_permission": classification.requested_permission,
        }
        if (
            classification.intent != "repository_access"
            or not classification.repository_name
            or classification.requested_permission in (None, Permission.NONE)
        ):
            delta["error_code"] = (
                "clarification_required"
                if classification.intent != "unsupported"
                else "unsupported_request"
            )
            delta["final_response"] = (
                "Please specify one repository and read, write, or admin "
                "access. The ticket was escalated for clarification; no access was changed."
            )
        return NodeResult(delta, classification.summary)

    def repository(self, state: AgentState, step: UUID) -> NodeResult:
        proposed = self.provider.select_repository_tool(state.repository_name)
        selected = RepositoryToolCall.model_validate(proposed.model_dump(mode="json"))
        if (
            selected.arguments.repository_name.casefold()
            != state.repository_name.strip().casefold()
        ):
            raise ProviderError(
                "invalid_tool_scope", "Selected repository differs from the request."
            )
        result = self.tool(step, selected.tool_name, selected.arguments.model_dump(mode="json"))
        if not result.succeeded:
            return NodeResult(
                {
                    "error_code": result.error_code,
                    "final_response": "The requested repository could not be found "
                    "unambiguously. Please confirm its name. No access was changed.",
                },
                "Repository lookup did not identify a valid resource.",
            )
        repository = RepositoryInfo.model_validate(result.output)
        return NodeResult(
            {"repository_id": str(repository.id), "repository": repository.model_dump(mode="json")},
            f"Repository {repository.name} identified.",
        )

    def current_access(self, state: AgentState, step: UUID) -> NodeResult:
        result = self.tool(
            step,
            "get_repository_permission",
            {
                "employee_id": state.employee_id,
                "repository_id": str(state.repository_id),
            },
        )
        if not result.succeeded:
            return NodeResult({"error_code": result.error_code}, "Current access check failed.")
        permission = PermissionInfo.model_validate(result.output)
        return NodeResult(
            {"current_permission": permission.permission.value},
            f"Current permission confirmed: {permission.permission.value}.",
        )

    def retrieve_policy(self, state: AgentState, step: UUID) -> NodeResult:
        from app.services.policy import policy_search_context

        query = policy_search_context(
            requested=state.requested_permission,
            current=state.current_permission,
            employee=state.employee,
            repository=state.repository,
        ).model_copy(update={"top_k": self.settings.rag_top_k})
        result = self.tool(step, "search_policies", query.model_dump(mode="json"))
        if not result.succeeded:
            return NodeResult(
                {
                    "error_code": "policy_retrieval_failed",
                    "final_response": "Current policy evidence could not be retrieved reliably. "
                    "The request requires manual review. No access was changed.",
                },
                "Policy retrieval failed; access changes are prohibited.",
            )
        retrieved = PolicySearchResult.model_validate(result.output)
        configured = state.retrieval_config
        if configured is None or (
            retrieved.embedding_provider != configured.provider
            or retrieved.embedding_model != configured.model
            or retrieved.dimensions != configured.dimensions
        ):
            return NodeResult(
                {
                    "error_code": "policy_retrieval_failed",
                    "final_response": "Policy evidence used a different embedding configuration "
                    "from this run. Manual review is required. No access was changed.",
                },
                "Policy evidence does not match the run's saved embedding configuration.",
            )
        if not retrieved.evidence:
            return NodeResult(
                {
                    "error_code": "policy_evidence_missing",
                    "final_response": "No relevant policy evidence was found. "
                    "The request requires manual review. No access was changed.",
                },
                "No relevant policy evidence was retrieved.",
            )
        titles = list(dict.fromkeys(item.title for item in retrieved.evidence))
        return NodeResult(
            {"retrieved_policies": [item.model_dump() for item in retrieved.evidence]},
            "Policy evidence retrieved: " + ", ".join(titles) + ".",
        )

    def legacy_plan(self, state: AgentState) -> NodeResult:
        if state.requested_permission == Permission.ADMIN:
            summary = "Admin access cannot be granted by ResolveAI; security review is required."
        else:
            summary = "Policy evaluation is not enabled in this release. Manual review is required."
        decision = DecisionSummary(disposition="escalate", summary=summary, policy_chunk_ids=[])
        return NodeResult(
            {
                "decision": decision.model_dump(mode="json"),
                "final_response": summary + " No access was changed.",
            },
            summary,
        )

    def plan(self, state: AgentState, _step: UUID) -> NodeResult:
        if state.workflow_version == 1:
            return self.legacy_plan(state)
        from app.services.policy import decide_access

        decision = decide_access(
            employee=state.employee,
            repository=state.repository,
            requested=state.requested_permission,
            current=state.current_permission,
            evidence=state.retrieved_policies,
        )
        next_step = {
            "approval_required": "Approval processing is not enabled in this release; "
            "the ticket was escalated for a recorded human review.",
            "grant": "Permission execution is not enabled in this release; "
            "the ticket was escalated to IT support.",
            "no_change": "Automatic verification and closure are not enabled in this release; "
            "the ticket was escalated for confirmation.",
        }.get(decision.disposition, "The ticket was escalated for manual review.")
        return NodeResult(
            {
                "decision": decision.model_dump(mode="json"),
                "final_response": f"{decision.summary} {next_step} No access was changed.",
            },
            decision.summary,
        )

    def escalate(self, state: AgentState, step: UUID) -> NodeResult:
        reason = state.final_response or (
            "The request could not be verified and was escalated for manual review. "
            "No access was changed."
        )
        result = self.tool(
            step, "escalate_ticket", {"ticket_id": str(state.ticket_id), "reason": reason}
        )
        if not result.succeeded:
            # Do not finish a run while its ticket has not reached a safe terminal state.
            raise RuntimeError("Ticket escalation failed.")
        return NodeResult(
            {
                "final_response": reason,
                "outcome": "failed"
                if state.error_code
                in {
                    "llm_timeout",
                    "llm_unavailable",
                    "invalid_model_output",
                    "invalid_agent_output",
                    "tool_failed",
                    "invalid_tool_scope",
                    "policy_retrieval_failed",
                }
                else "escalated",
            },
            "Ticket escalated; no permissions were changed.",
        )

    def compile(self, checkpointer: BaseCheckpointSaver, *, workflow_version: int = 2):
        graph = StateGraph(AgentState)
        nodes = [
            (WorkflowNode.RECEIVE_REQUEST, self.receive),
            (WorkflowNode.IDENTIFY_EMPLOYEE, self.employee),
            (WorkflowNode.CLASSIFY_REQUEST, self.classify),
            (WorkflowNode.IDENTIFY_RESOURCE, self.repository),
            (WorkflowNode.CHECK_CURRENT_ACCESS, self.current_access),
        ]
        if workflow_version >= 2:
            nodes.append((WorkflowNode.RETRIEVE_POLICY, self.retrieve_policy))
        nodes.extend(
            [(WorkflowNode.PLAN_ACTION, self.plan), (WorkflowNode.ESCALATE, self.escalate)]
        )
        for sequence, (node, operation) in enumerate(nodes, start=1):
            graph.add_node(node.value, self.audited(node, sequence, operation))
        graph.add_edge(START, WorkflowNode.RECEIVE_REQUEST.value)
        graph.add_edge(WorkflowNode.RECEIVE_REQUEST.value, WorkflowNode.IDENTIFY_EMPLOYEE.value)
        for index in range(1, len(nodes) - 2):
            graph.add_conditional_edges(
                nodes[index][0].value,
                lambda state: "error" if state.error_code else "next",
                {"error": WorkflowNode.ESCALATE.value, "next": nodes[index + 1][0].value},
            )
        graph.add_edge(WorkflowNode.PLAN_ACTION.value, WorkflowNode.ESCALATE.value)
        graph.add_edge(WorkflowNode.ESCALATE.value, END)
        return graph.compile(checkpointer=checkpointer)
