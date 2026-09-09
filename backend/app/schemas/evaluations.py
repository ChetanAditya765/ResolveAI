from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.contracts import EvaluationAssertion, EvaluationTrace, ExpectedOutcome


class EvaluationScenarioRead(ExpectedOutcome):
    id: str
    name: str
    description: str


class EvaluationSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    submission_key: UUID = Field(default_factory=uuid4)


class EvaluationBatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    requested_by_id: UUID
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    scenario_ids: list[str]
    catalog_version: str
    evaluator_version: str
    total_count: int
    completed_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


class EvaluationRead(BaseModel):
    id: UUID
    run_id: UUID | None
    batch_id: UUID | None
    source: Literal["live", "scenario"]
    scenario_id: str
    evaluator_version: str
    created_at: datetime
    passed: bool
    metrics: dict[str, float | None]
    assertions: list[EvaluationAssertion]
    latency_ms: int | None
    human_wait_ms: int | None
    observed: dict[str, Any]


class EvaluationDetail(EvaluationRead):
    trace: EvaluationTrace
    expected: ExpectedOutcome | None


class EvaluationSummary(BaseModel):
    source: Literal["live", "scenario"]
    batch_id: UUID | None
    evaluator_version: str
    total_scenarios: int
    total_results: int
    passed_results: int
    selected_scenarios: int | None
    metrics: dict[str, float | None]
    metric_samples: dict[str, int]
    average_latency_ms: float | None
    average_human_wait_ms: float | None
