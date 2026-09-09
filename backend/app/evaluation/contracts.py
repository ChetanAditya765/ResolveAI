from typing import Any

from pydantic import BaseModel, ConfigDict, Field

EVALUATOR_VERSION = "deterministic-v2"
CATALOG_VERSION = "access-v1"


class EvaluationTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: dict[str, Any]
    ticket: dict[str, Any]
    steps: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    initial_permission: str | None = None
    final_permission: str | None = None


class ExpectedOutcome(BaseModel):
    model_config = ConfigDict(extra="ignore")
    expected_outcome: str
    expected_final_status: str
    expected_tools: list[str]
    forbidden_tools: list[str]
    requires_approval: bool
    expected_permission: str | None = None
    expected_policy_sections: list[str] = Field(default_factory=list)


class EvaluationAssertion(BaseModel):
    name: str
    passed: bool | None
    summary: str
    expected: Any = None
    observed: Any = None


class EvaluationScore(BaseModel):
    metrics: dict[str, float | None]
    assertions: list[EvaluationAssertion]
    passed: bool
    latency_ms: int | None
    human_wait_ms: int | None
