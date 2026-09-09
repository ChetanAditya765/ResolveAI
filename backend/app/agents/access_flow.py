from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.graph import AccessWorkflow, NodeResult
from app.agents.state import (
    AgentState,
    ExecutionResult,
    PlannedAccessChange,
    VerificationResult,
    WorkflowNode,
)
from app.models import ApprovalStatus, Permission
from app.rag.contracts import PolicySearch, PolicySearchResult
from app.services.policy import decide_access, has_completion_evidence
from app.tools.access_schemas import ApprovalInfo, CloseResult, GrantResult
from app.tools.schemas import PermissionInfo

RANK = {Permission.NONE: 0, Permission.READ: 1, Permission.WRITE: 2, Permission.ADMIN: 3}


class AccessFlow(AccessWorkflow):
    """Version 3: policy-grounded execution with a durable human decision boundary."""

    def tool(self, step_id: UUID, name: str, arguments: dict, *, suffix: str = ""):
        key = f"{self.run_id}:{step_id}:{name}:{suffix}"
        if name == "grant_repository_permission":
            key = f"{self.run_id}:grant_repository_permission"
        return self.tools.execute(
            run_id=self.run_id,
            step_id=step_id,
            tool_name=name,
            arguments=arguments,
            idempotency_key=key,
            lease_owner=self.owner,
        )

    def retrieve_policy(self, state: AgentState, step: UUID) -> NodeResult:
        primary = super().retrieve_policy(state, step)
        if primary.delta.get("error_code"):
            return primary
        query = PolicySearch(
            query="Least Privilege Policy Existing permissions already sufficient read write",
            top_k=self.settings.rag_top_k,
            document_slugs=["least-privilege-policy"],
        )
        result = self.tool(
            step, "search_policies", query.model_dump(mode="json"), suffix="completion"
        )
        if not result.succeeded:
            return self.failed(
                "policy_retrieval_failed", "Completion policy evidence could not be retrieved."
            )
        extra = PolicySearchResult.model_validate(result.output)
        configured = state.retrieval_config
        if (
            extra.embedding_provider != configured.provider
            or extra.embedding_model != configured.model
            or extra.dimensions != configured.dimensions
        ):
            return self.failed(
                "policy_retrieval_failed",
                "Completion evidence used an unexpected embedding configuration.",
            )
        by_id = {item["chunk_id"]: item for item in primary.delta["retrieved_policies"]}
        by_id.update({item.chunk_id: item.model_dump(mode="json") for item in extra.evidence})
        evidence = list(by_id.values())
        return NodeResult(
            {"retrieved_policies": evidence},
            "Retrieved authorization and least-privilege policy evidence.",
        )

    def plan(self, state: AgentState, _step: UUID) -> NodeResult:
        decision = decide_access(
            state.employee,
            state.repository,
            state.requested_permission,
            state.current_permission,
            state.retrieved_policies,
        )
        if decision.disposition != "escalate" and not has_completion_evidence(
            state.retrieved_policies
        ):
            return self.failed(
                "policy_evidence_missing", "Required completion policy evidence is missing."
            )
        planned = None
        if decision.disposition in {"grant", "approval_required"}:
            planned = PlannedAccessChange(
                employee_id=state.employee_id,
                repository_id=str(state.repository_id),
                permission=state.requested_permission,
                idempotency_key=f"{self.run_id}:grant_repository_permission",
            ).model_dump(mode="json")
        return NodeResult(
            {
                "decision": decision.model_dump(mode="json"),
                "planned_action": planned,
                "final_response": decision.summary if decision.disposition == "escalate" else None,
            },
            decision.summary,
        )

    @staticmethod
    def failed(code: str, message: str) -> NodeResult:
        return NodeResult(
            {
                "error_code": code,
                "final_response": message
                + " IT support must review this ticket; successful access is not confirmed.",
            },
            message,
        )

    @staticmethod
    def access_arguments(state: AgentState) -> dict:
        return {
            "employee_id": state.employee_id,
            "repository_id": str(state.repository_id),
            "permission": state.requested_permission.value,
        }

    def request_approval(self, state: AgentState, step: UUID) -> NodeResult:
        result = self.tool(step, "create_approval_request", self.access_arguments(state))
        if not result.succeeded:
            return self.failed(
                result.error_code,
                "A manager approval request could not be created. No access was changed.",
            )
        approval = ApprovalInfo.model_validate(result.output)
        return NodeResult(
            {"approval_id": str(approval.id), "approval_status": approval.status.value},
            "Manager approval requested for the exact employee, repository, and permission.",
        )

    def await_approval(self, state: AgentState, _step: UUID) -> NodeResult:
        # This node is deliberately side-effect free apart from the timeline wrapper.
        # The resume value is a notification, never an authorization decision.
        interrupt({"approval_id": str(state.approval_id), "action": "review_repository_access"})
        return NodeResult(
            {}, "A human decision was recorded; checking the permanent approval record."
        )

    def check_approval(self, state: AgentState, step: UUID) -> NodeResult:
        result = self.tool(step, "get_approval_status", {"approval_id": str(state.approval_id)})
        if not result.succeeded:
            return self.failed(result.error_code, "The approval record could not be verified.")
        approval = ApprovalInfo.model_validate(result.output)
        if approval.status == ApprovalStatus.REJECTED:
            return NodeResult(
                {
                    "approval_status": "REJECTED",
                    "outcome": "rejected",
                    "final_response": "Your access request was rejected by the reviewer. "
                    "No permission was changed. The ticket was escalated for follow-up.",
                },
                "Approval rejected; permission changes are prohibited.",
            )
        if approval.status != ApprovalStatus.APPROVED:
            return self.failed(
                "approval_not_granted", "A recorded approval is required before execution."
            )
        return NodeResult(
            {"approval_status": "APPROVED"},
            "Approval granted; the permission tool will recheck policy and reviewer eligibility.",
        )

    def execute_action(self, state: AgentState, step: UUID) -> NodeResult:
        arguments = {
            **self.access_arguments(state),
            "approval_id": str(state.approval_id) if state.approval_id else None,
        }
        result = self.tool(step, "grant_repository_permission", arguments)
        execution = ExecutionResult(
            tool_execution_id=str(result.execution_id),
            succeeded=result.succeeded,
            error_code=result.error_code,
        )
        if not result.succeeded:
            failure = self.failed(
                result.error_code, "The permission update did not complete successfully."
            )
            failure.delta["execution_result"] = execution.model_dump(mode="json")
            return failure
        grant = GrantResult.model_validate(result.output)
        execution.changed = grant.changed
        return NodeResult(
            {"execution_result": execution.model_dump(mode="json")},
            "Permission updated; independent verification is required."
            if grant.changed
            else "Existing permission is sufficient; no change was needed.",
        )

    def verify_action(self, state: AgentState, step: UUID) -> NodeResult:
        result = self.tool(
            step,
            "get_repository_permission",
            {
                "employee_id": state.employee_id,
                "repository_id": str(state.repository_id),
            },
        )
        if not result.succeeded:
            return self.failed("verification_failed", "The permission verification tool failed.")
        observed = PermissionInfo.model_validate(result.output)
        sufficient = (
            observed.employee_id == state.employee_id
            and observed.repository_id == state.repository_id
            and RANK[observed.permission] >= RANK[state.requested_permission]
        )
        verification = VerificationResult(
            tool_execution_id=str(result.execution_id),
            observed_permission=observed.permission,
            sufficient=sufficient,
        )
        delta = {"verification_result": verification.model_dump(mode="json")}
        if not sufficient:
            delta.update(
                self.failed(
                    "verification_mismatch", "Observed permission does not satisfy the request."
                ).delta
            )
        return NodeResult(
            delta,
            f"Permission verified: {observed.permission.value}."
            if sufficient
            else "Permission verification did not confirm the requested access.",
        )

    def respond(self, state: AgentState, _step: UUID) -> NodeResult:
        return NodeResult({}, "Verification confirmed; preparing the ticket's final response.")

    def resolve(self, state: AgentState, step: UUID) -> NodeResult:
        result = self.tool(step, "close_ticket", {"ticket_id": str(state.ticket_id)})
        if not result.succeeded:
            return self.failed(
                result.error_code, "The ticket could not be closed with verified current access."
            )
        closed = CloseResult.model_validate(result.output)
        changed = state.execution_result and state.execution_result.changed
        return NodeResult(
            {
                "final_response": closed.final_response,
                "outcome": "granted" if changed else "already_sufficient",
            },
            "Ticket resolved with verified repository permission.",
        )

    def escalate(self, state: AgentState, step: UUID) -> NodeResult:
        reason = (
            state.final_response
            or "The request requires manual review; successful access is not confirmed."
        )
        result = self.tool(
            step, "escalate_ticket", {"ticket_id": str(state.ticket_id), "reason": reason}
        )
        if not result.succeeded:
            raise RuntimeError("Ticket escalation failed.")
        safe_codes = {
            None,
            "repository_not_found",
            "clarification_required",
            "unsupported_request",
            "employee_inactive",
            "manager_unavailable",
            "policy_evidence_missing",
        }
        outcome = (
            "rejected"
            if state.outcome == "rejected"
            else "escalated"
            if state.error_code in safe_codes
            else "failed"
        )
        return NodeResult(
            {"final_response": reason, "outcome": outcome},
            "Ticket escalated with the recorded outcome.",
        )

    def compile(self, checkpointer, *, workflow_version: int = 3):
        graph = StateGraph(AgentState)
        nodes = [
            (WorkflowNode.RECEIVE_REQUEST, self.receive),
            (WorkflowNode.IDENTIFY_EMPLOYEE, self.employee),
            (WorkflowNode.CLASSIFY_REQUEST, self.classify),
            (WorkflowNode.IDENTIFY_RESOURCE, self.repository),
            (WorkflowNode.CHECK_CURRENT_ACCESS, self.current_access),
            (WorkflowNode.RETRIEVE_POLICY, self.retrieve_policy),
            (WorkflowNode.PLAN_ACTION, self.plan),
            (WorkflowNode.REQUEST_APPROVAL, self.request_approval),
            (WorkflowNode.AWAIT_APPROVAL, self.await_approval),
            (WorkflowNode.CHECK_APPROVAL, self.check_approval),
            (WorkflowNode.EXECUTE_ACTION, self.execute_action),
            (WorkflowNode.VERIFY_ACTION, self.verify_action),
            (WorkflowNode.RESPOND, self.respond),
            (WorkflowNode.RESOLVE, self.resolve),
            (WorkflowNode.ESCALATE, self.escalate),
        ]
        for sequence, (node, operation) in enumerate(nodes, 1):
            graph.add_node(node.value, self.audited(node, sequence, operation))
        graph.add_edge(START, "RECEIVE_REQUEST")
        graph.add_edge("RECEIVE_REQUEST", "IDENTIFY_EMPLOYEE")
        for index in range(1, 6):
            graph.add_conditional_edges(
                nodes[index][0].value,
                lambda s: "error" if s.error_code else "next",
                {"error": "ESCALATE", "next": nodes[index + 1][0].value},
            )
        graph.add_conditional_edges(
            "PLAN_ACTION",
            lambda s: "escalate" if s.error_code or not s.decision else s.decision.disposition,
            {
                "escalate": "ESCALATE",
                "clarify": "ESCALATE",
                "grant": "EXECUTE_ACTION",
                "no_change": "VERIFY_ACTION",
                "approval_required": "REQUEST_APPROVAL",
            },
        )
        graph.add_conditional_edges(
            "REQUEST_APPROVAL",
            lambda s: "error" if s.error_code else "next",
            {"error": "ESCALATE", "next": "AWAIT_APPROVAL"},
        )
        graph.add_edge("AWAIT_APPROVAL", "CHECK_APPROVAL")
        graph.add_conditional_edges(
            "CHECK_APPROVAL",
            lambda s: (
                "next"
                if not s.error_code and s.approval_status == ApprovalStatus.APPROVED
                else "error"
            ),
            {"next": "EXECUTE_ACTION", "error": "ESCALATE"},
        )
        for source, target in [
            ("EXECUTE_ACTION", "VERIFY_ACTION"),
            ("VERIFY_ACTION", "RESPOND"),
            ("RESPOND", "RESOLVE"),
        ]:
            graph.add_conditional_edges(
                source,
                lambda s: "error" if s.error_code else "next",
                {"error": "ESCALATE", "next": target},
            )
        graph.add_conditional_edges(
            "RESOLVE",
            lambda s: "error" if s.error_code else "done",
            {"error": "ESCALATE", "done": END},
        )
        graph.add_edge("ESCALATE", END)
        return graph.compile(checkpointer=checkpointer)
