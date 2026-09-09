from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import Permission, RepositorySensitivity


class ToolSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


class EmployeeLookup(ToolSchema):
    employee_id: str = Field(min_length=1, max_length=32)


class RepositoryLookup(ToolSchema):
    repository_name: str = Field(min_length=1, max_length=120)


class PermissionLookup(EmployeeLookup):
    repository_id: UUID


class EscalationInput(ToolSchema):
    ticket_id: UUID
    reason: str = Field(min_length=3, max_length=2000)


class EmployeeInfo(ToolSchema):
    id: str
    name: str
    email: str
    department: str
    role: str
    manager_id: str | None
    is_active: bool


class RepositoryInfo(ToolSchema):
    id: UUID
    name: str
    owning_department: str
    sensitivity_level: RepositorySensitivity


class PermissionInfo(ToolSchema):
    employee_id: str
    repository_id: UUID
    permission: Permission


class EscalationResult(ToolSchema):
    ticket_id: UUID
    status: Literal["ESCALATED"] = "ESCALATED"
    final_response: str


class ToolResult(ToolSchema):
    execution_id: UUID
    tool_name: str
    succeeded: bool
    output: dict
    error_code: str | None = None
    summary: str
