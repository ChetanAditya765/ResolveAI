from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import UserRead
from app.tools.access_schemas import ApprovalInfo
from app.tools.schemas import EmployeeInfo, RepositoryInfo


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    comment: str | None = Field(default=None, max_length=2000)


class ApprovalRead(ApprovalInfo):
    employee: EmployeeInfo
    repository: RepositoryInfo
    approver: UserRead
    can_decide: bool
