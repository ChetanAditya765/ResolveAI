from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models import TicketStatus

RequestText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=8000)
]
EmployeeID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: EmployeeID
    request_text: RequestText


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_text: RequestText


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    employee_id: str
    request_text: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    final_response: str | None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    role: str
    content: str
    created_at: datetime


class TicketDetail(TicketRead):
    messages: list[MessageRead]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
