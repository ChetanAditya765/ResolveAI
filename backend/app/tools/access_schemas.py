from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models import ApprovalStatus, Permission
from app.rag.contracts import PolicyEvidence
from app.tools.schemas import ToolSchema


class AccessRequest(ToolSchema):
    employee_id: str = Field(min_length=1, max_length=32)
    repository_id: UUID
    permission: Permission


class GrantRequest(AccessRequest):
    approval_id: UUID | None = None


class ApprovalLookup(ToolSchema):
    approval_id: UUID


class ApprovalInfo(ToolSchema):
    id: UUID
    run_id: UUID
    ticket_id: UUID
    employee_id: str
    repository_id: UUID
    permission: Permission
    approver_id: UUID
    status: ApprovalStatus
    policy_evidence: list[PolicyEvidence]
    recommendation: str
    requested_at: datetime
    decided_at: datetime | None
    decided_by_id: UUID | None
    decision_comment: str | None


class GrantResult(ToolSchema):
    employee_id: str
    repository_id: UUID
    previous_permission: Permission
    observed_permission: Permission
    changed: bool


class CloseRequest(ToolSchema):
    ticket_id: UUID


class CloseResult(ToolSchema):
    ticket_id: UUID
    final_response: str
    observed_permission: Permission
