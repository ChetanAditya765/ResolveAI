from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.config import Settings
from app.evaluation.contracts import ExpectedOutcome
from app.evaluation.harness import run_scenario
from app.evaluation.scenarios import SCENARIOS, Fault
from app.evaluation.scoring import score_trace


def test_catalog_has_distinct_reviewable_expectations():
    assert len(SCENARIOS) >= 25
    assert len({item.id for item in SCENARIOS}) == len(SCENARIOS)
    assert (
        len(
            {
                (item.employee_id, item.request_text, item.fault, item.initial_permission)
                for item in SCENARIOS
            }
        )
        >= 25
    )
    assert {item.fault for item in SCENARIOS} == set(Fault)
    for item in SCENARIOS:
        public = item.model_dump(mode="json")
        assert (
            not {"fault", "initial_permission", "approval_decision", "repository_name"}
            & public.keys()
        )
        assert item.expected_tools
        assert not set(item.expected_tools) & set(item.forbidden_tools)
        assert item.description


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.id)
def test_real_workflow_scenario(scenario, policy_directory):
    settings = Settings(_env_file=None, app_env="test", policy_directory=policy_directory)
    trace = run_scenario(scenario, settings)
    score = score_trace(trace, ExpectedOutcome.model_validate(scenario.model_dump()))
    failures = [item.model_dump() for item in score.assertions if item.passed is False]
    assert score.passed, {"failed_assertions": failures, "state": trace.run["state"]}
    assert score.metrics["task_success"] == 1
    assert score.metrics["hallucination_or_invalid_resource_rate"] == 0
    assert trace.run["provider_name"] == "demo"
    assert trace.run["state"]["retrieval_config"]["provider"] == "local_hash"


def test_concurrent_cases_do_not_share_permissions_or_faults(policy_directory):
    settings = Settings(_env_file=None, app_env="test", policy_directory=policy_directory)
    catalog = {item.id: item for item in SCENARIOS}
    cases = [catalog["grant_failure"], catalog["write_payments_approved"]]
    with ThreadPoolExecutor(max_workers=2) as executor:
        traces = list(executor.map(lambda case: run_scenario(case, settings), cases))
    assert [trace.final_permission for trace in traces] == ["read", "write"]
    assert traces[0].ticket["id"] != traces[1].ticket["id"]
    assert traces[0].run["id"] != traces[1].run["id"]
    assert all(
        score_trace(trace, ExpectedOutcome.model_validate(case.model_dump())).passed
        for case, trace in zip(cases, traces, strict=True)
    )
