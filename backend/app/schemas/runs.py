from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.agents.state import AgentState
from app.models import AgentRunStatus, AgentStepStatus, ToolExecutionStatus


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    status: AgentRunStatus
    state: AgentState
    provider_name: str
    model_name: str
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    attempt_count: int
    error: str | None


class RunAccepted(BaseModel):
    run_id: UUID
    ticket_id: UUID
    status: AgentRunStatus


class StepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sequence: int
    node: str
    status: AgentStepStatus
    summary: str
    details: dict
    started_at: datetime
    completed_at: datetime | None
    latency_ms: int | None


class ToolExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    step_id: UUID | None
    tool_name: str
    arguments: dict
    result: dict | None
    status: ToolExecutionStatus
    error: str | None
    started_at: datetime
    completed_at: datetime | None
    latency_ms: int | None
