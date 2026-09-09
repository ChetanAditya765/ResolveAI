from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EmployeeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    email: str
    department: str
    role: str
    manager_id: str | None
    is_active: bool


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    owning_department: str
    sensitivity_level: str
