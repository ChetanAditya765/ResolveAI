from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models import UserRole


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: str
    name: str
    email: str
    role: UserRole


class SessionRead(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserRead
