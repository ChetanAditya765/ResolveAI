from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.models import Permission


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["repository_access", "unsupported", "ambiguous"]
    repository_name: str | None
    requested_permission: Permission | None
    summary: str = Field(max_length=500)


class RepositoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    repository_name: str = Field(min_length=1, max_length=120)


class RepositoryToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: Literal["find_repository"]
    arguments: RepositoryArguments


class ProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AgentProvider(Protocol):
    provider_name: str
    model_name: str

    def classify(self, request_text: str) -> Classification: ...

    def select_repository_tool(self, repository_name: str) -> RepositoryToolCall: ...
