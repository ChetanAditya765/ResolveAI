from typing import Any, Literal

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = "0.1.0"


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    database: str = "connected"
    schema_revision: str
    llm_provider: Literal["demo", "openai"]
    embedding_provider: Literal["local_hash", "openai"]
    demo_auth_enabled: bool
    agent_worker_enabled: bool
