"""Validated public state for the explicit access workflow."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import ApprovalStatus, Permission
from app.rag.contracts import PolicyEvidence
from app.tools.schemas import EmployeeInfo, RepositoryInfo


class WorkflowNode(StrEnum):
    RECEIVE_REQUEST = "RECEIVE_REQUEST"
    CLASSIFY_REQUEST = "CLASSIFY_REQUEST"
    IDENTIFY_EMPLOYEE = "IDENTIFY_EMPLOYEE"
    IDENTIFY_RESOURCE = "IDENTIFY_RESOURCE"
    RETRIEVE_POLICY = "RETRIEVE_POLICY"
    CHECK_CURRENT_ACCESS = "CHECK_CURRENT_ACCESS"
    PLAN_ACTION = "PLAN_ACTION"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    AWAIT_APPROVAL = "AWAIT_APPROVAL"
    CHECK_APPROVAL = "CHECK_APPROVAL"
    EXECUTE_ACTION = "EXECUTE_ACTION"
    VERIFY_ACTION = "VERIFY_ACTION"
    RESPOND = "RESPOND"
    RESOLVE = "RESOLVE"
    ESCALATE = "ESCALATE"
    ERROR = "ERROR"


class PlannedAccessChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: Literal["grant_repository_permission"] = "grant_repository_permission"
    employee_id: str
    repository_id: str
    permission: Permission
    idempotency_key: str


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_execution_id: str
    succeeded: bool
    error_code: str | None = None
    changed: bool | None = None


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_execution_id: str
    observed_permission: Permission
    sufficient: bool


class DecisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disposition: Literal["no_change", "grant", "approval_required", "escalate", "clarify"]
    summary: str = Field(max_length=1000)
    policy_chunk_ids: list[str]
    # Only concise policy rationale belongs here; never hidden model reasoning.


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["local_hash", "openai"]
    model: str
    dimensions: Literal[1536] = 1536
    top_k: int = Field(ge=1, le=20)
    min_score: float = Field(ge=0, le=1)


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    workflow_version: Literal[1, 2, 3] = 1
    retrieval_config: RetrievalConfig | None = None
    ticket_id: UUID
    run_id: UUID
    graph_thread_id: str
    request_text: str
    current_node: WorkflowNode = WorkflowNode.RECEIVE_REQUEST
    intent: Literal["repository_access", "unsupported", "ambiguous"] | None = None
    employee_id: str | None = None
    repository_name: str | None = None
    repository_id: UUID | None = None
    requested_permission: Permission | None = None
    current_permission: Permission | None = None
    employee: EmployeeInfo | None = None
    repository: RepositoryInfo | None = None
    retrieved_policies: list[PolicyEvidence] = Field(default_factory=list)
    decision: DecisionSummary | None = None
    planned_action: PlannedAccessChange | None = None
    approval_id: UUID | None = None
    approval_status: ApprovalStatus | None = None
    execution_result: ExecutionResult | None = None
    verification_result: VerificationResult | None = None
    final_response: str | None = None
    outcome: Literal["granted", "already_sufficient", "rejected", "escalated", "failed"] | None = (
        None
    )
    error_code: str | None = None
