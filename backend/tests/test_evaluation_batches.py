from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.base import utc_now
from app.evaluation import service
from app.evaluation.contracts import ExpectedOutcome
from app.models import (
    AgentRun,
    AgentStep,
    ApprovalRequest,
    ConversationMessage,
    EvaluationBatch,
    EvaluationResult,
    RepositoryPermission,
    Ticket,
    ToolExecution,
)
from app.schemas.evaluations import EvaluationBatchRead, EvaluationDetail, EvaluationSummary


@pytest.fixture
def batch_settings(tmp_path, policy_directory):
    return Settings(
        app_env="test",
        database_url="sqlite+pysqlite://",
        checkpoint_sqlite_path=tmp_path / "application-checkpoints.sqlite",
        policy_directory=policy_directory,
        llm_provider="demo",
        embedding_provider="local_hash",
    )


@pytest.fixture
def selected_cases():
    from app.evaluation.scenarios import SCENARIOS

    automatic = next(
        case
        for case in SCENARIOS
        if case.expected_outcome == "granted"
        and case.expected_final_status == "RESOLVED"
        and not case.requires_approval
    )
    approved = next(
        case
        for case in SCENARIOS
        if case.expected_outcome == "granted"
        and case.expected_final_status == "RESOLVED"
        and case.requires_approval
    )
    return [automatic.id, approved.id]


def login(client, employee_id="EMP002"):
    users = client.get("/api/demo/users").json()
    user = next(item for item in users if item["employee_id"] == employee_id)
    response = client.post("/api/demo/session", json={"user_id": user["id"]})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def submit(client, scenario_ids, *, headers=None, key=None):
    body = {"scenario_ids": scenario_ids, "submission_key": str(key or uuid4())}
    response = client.post("/api/evaluations/run", json=body, headers=headers or login(client))
    assert response.status_code == 202, response.text
    EvaluationBatchRead.model_validate(response.json())
    assert response.headers["Location"] == (f"/api/evaluations/batches/{response.json()['id']}")
    return response.json()


def process(engine, settings, owner="test-evaluation-worker"):
    return service.process_next(sessionmaker(bind=engine), settings, owner)


def counts(session):
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model))
        for model in (
            Ticket,
            AgentRun,
            AgentStep,
            ToolExecution,
            ApprovalRequest,
            ConversationMessage,
        )
    }


def permissions(session):
    return list(
        session.execute(
            select(
                RepositoryPermission.id,
                RepositoryPermission.employee_id,
                RepositoryPermission.repository_id,
                RepositoryPermission.permission,
                RepositoryPermission.updated_at,
            ).order_by(RepositoryPermission.id)
        ).all()
    )


def test_catalog_exposes_expected_properties_without_fault_injection_settings(client):
    response = client.get("/api/evaluations/scenarios")
    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog) >= 25
    assert len({item["id"] for item in catalog}) == len(catalog)
    TypeAdapter(list[ExpectedOutcome]).validate_python(catalog)
    for item in catalog:
        assert item["name"] and item["description"] and item["expected_outcome"]
        assert set(item["expected_tools"]).isdisjoint(item["forbidden_tools"])
        assert not {"fault", "approval_decision", "initial_permission"} & item.keys()
    assert {item["requires_approval"] for item in catalog} == {True, False}
    assert {"RESOLVED", "ESCALATED"} <= {item["expected_final_status"] for item in catalog}


@pytest.mark.parametrize(
    ("employee_id", "expected_status"),
    [(None, 401), ("EMP001", 403), ("EMP002", 202), ("EMP003", 202)],
)
def test_only_manager_or_admin_can_submit(client, employee_id, expected_status):
    headers = login(client, employee_id) if employee_id else {}
    response = client.post("/api/evaluations/run", json={}, headers=headers)
    assert response.status_code == expected_status, response.text
    if expected_status == 202:
        body = EvaluationBatchRead.model_validate(response.json())
        assert body.status == "PENDING"
        assert body.total_count == len(client.get("/api/evaluations/scenarios").json())
        assert body.completed_count == 0
    else:
        assert client.get("/api/evaluations/batches").json()["total"] == 0


def test_submission_retry_is_idempotent_even_if_selection_order_changes(client, selected_cases):
    key = uuid4()
    first = submit(client, selected_cases, key=key)
    retry = submit(client, list(reversed(selected_cases)), key=key)
    assert retry == first
    assert client.get("/api/evaluations/batches").json()["total"] == 1
    assert first["scenario_ids"] == sorted(selected_cases)


@pytest.mark.parametrize("conflict", ["scenario_selection", "requesting_actor"])
def test_submission_key_cannot_be_reused_for_a_different_request(client, selected_cases, conflict):
    key = uuid4()
    first = submit(client, selected_cases, key=key)
    selection = selected_cases[:1] if conflict == "scenario_selection" else selected_cases
    actor = "EMP003" if conflict == "requesting_actor" else "EMP002"
    response = client.post(
        "/api/evaluations/run",
        headers=login(client, actor),
        json={"scenario_ids": selection, "submission_key": str(key)},
    )
    assert response.status_code == 409, response.text
    assert "evaluation_key_conflict" in response.text
    assert client.get(f"/api/evaluations/batches/{first['id']}").json() == first


def test_only_one_batch_is_active_at_a_time(client, selected_cases):
    first = submit(client, selected_cases)
    response = client.post("/api/evaluations/run", json={}, headers=login(client, "EMP003"))
    assert response.status_code == 409
    assert "evaluation_batch_active" in response.text
    assert client.get("/api/evaluations/batches").json()["items"] == [first]


@pytest.mark.parametrize("selection", [[], ["invented-scenario"], ["duplicate", "duplicate"]])
def test_invalid_catalog_selection_does_not_create_a_batch(client, selection):
    response = client.post(
        "/api/evaluations/run", json={"scenario_ids": selection}, headers=login(client)
    )
    assert response.status_code == 422
    assert client.get("/api/evaluations/batches").json()["total"] == 0


def test_known_scenario_cannot_be_selected_twice(client, selected_cases):
    response = client.post(
        "/api/evaluations/run",
        json={"scenario_ids": [selected_cases[0], selected_cases[0]]},
        headers=login(client),
    )
    assert response.status_code == 422
    assert "invalid_scenarios" in response.text


@pytest.mark.parametrize("source", ["live", "scenario"])
def test_empty_summary_reports_unknown_metrics_instead_of_perfect_scores(client, source):
    response = client.get("/api/evaluations/summary", params={"source": source})
    assert response.status_code == 200, response.text
    summary = EvaluationSummary.model_validate(response.json())
    assert summary.total_scenarios >= 25
    assert summary.total_results == summary.passed_results == 0
    assert summary.batch_id is None
    assert summary.selected_scenarios is None
    assert set(summary.metrics) == set(service.METRICS)
    assert set(summary.metrics.values()) == {None}
    assert set(summary.metric_samples.values()) == {0}
    assert summary.average_latency_ms is None
    assert summary.average_human_wait_ms is None
    assert client.get("/api/evaluations/results", params={"source": source}).json()["total"] == 0


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        (f"/api/evaluations/batches/{uuid4()}", 404),
        (f"/api/evaluations/results/{uuid4()}", 404),
        (f"/api/evaluations/results?source=scenario&batch_id={uuid4()}", 404),
        (f"/api/evaluations/summary?source=scenario&batch_id={uuid4()}", 404),
        (f"/api/evaluations/results?source=live&batch_id={uuid4()}", 422),
        (f"/api/evaluations/summary?source=live&batch_id={uuid4()}", 422),
        ("/api/evaluations/results?source=unknown", 422),
        ("/api/evaluations/results?limit=101", 422),
        ("/api/evaluations/batches?offset=-1", 422),
    ],
)
def test_result_filters_and_missing_resources_have_proper_status_codes(
    client, path, expected_status
):
    assert client.get(path).status_code == expected_status


def test_batch_executes_one_real_case_per_claim_and_preserves_application_data(
    client, engine, db_session, batch_settings, selected_cases
):
    ticket = client.post(
        "/api/tickets",
        json={"employee_id": "EMP001", "request_text": "Write access to payments"},
    )
    assert ticket.status_code == 201, ticket.text
    before_counts, before_permissions = counts(db_session), permissions(db_session)
    batch = submit(client, selected_cases)
    batch_id = UUID(batch["id"])

    assert process(engine, batch_settings) == batch_id
    first_progress = client.get(f"/api/evaluations/batches/{batch_id}").json()
    assert first_progress["status"] == "RUNNING"
    assert first_progress["completed_count"] == 1
    assert first_progress["started_at"] is not None
    assert first_progress["completed_at"] is None
    first_results = client.get(
        "/api/evaluations/results", params={"source": "scenario", "batch_id": str(batch_id)}
    ).json()
    assert first_results["total"] == 1
    first_result_id = first_results["items"][0]["id"]

    # A fresh worker resumes from committed results rather than repeating the first case.
    assert process(engine, batch_settings, "replacement-worker") == batch_id
    completed = client.get(f"/api/evaluations/batches/{batch_id}").json()
    assert completed["status"] == "COMPLETED"
    assert completed["completed_count"] == completed["total_count"] == 2
    assert completed["completed_at"] is not None
    assert completed["error"] is None
    assert process(engine, batch_settings) is None

    parameters = {"source": "scenario", "batch_id": str(batch_id), "limit": 1}
    first_page = client.get("/api/evaluations/results", params=parameters).json()
    second_page = client.get("/api/evaluations/results", params={**parameters, "offset": 1}).json()
    assert first_page["total"] == second_page["total"] == 2
    rows = first_page["items"] + second_page["items"]
    assert len({row["id"] for row in rows}) == 2
    assert first_result_id in {row["id"] for row in rows}
    assert {row["scenario_id"] for row in rows} == set(selected_cases)
    for row in rows:
        assert row["source"] == "scenario" and row["run_id"] is None
        assert row["passed"], row["assertions"]
        assert row["metrics"]["task_success"] == 1
        response = client.get(f"/api/evaluations/results/{row['id']}")
        detail = EvaluationDetail.model_validate(response.json())
        assert detail.expected is not None
        assert detail.trace.tools and detail.trace.steps
        assert detail.trace.ticket["status"] == "RESOLVED"
        assert detail.trace.run["state"]["outcome"] == "granted"
        assert detail.trace.ticket["id"] != ticket.json()["id"]
        assert any(
            tool["tool_name"] == "grant_repository_permission" for tool in detail.trace.tools
        )

    summary = client.get("/api/evaluations/summary", params={"source": "scenario"}).json()
    assert summary["batch_id"] == str(batch_id)
    assert summary["total_results"] == summary["passed_results"] == 2
    assert summary["selected_scenarios"] == 2
    assert summary["metrics"]["task_success"] == 1
    assert summary["metric_samples"]["task_success"] == 2
    assert summary["average_latency_ms"] >= 0
    assert client.get("/api/evaluations/results", params={"source": "live"}).json()["total"] == 0
    assert client.get("/api/evaluations/summary").json()["total_results"] == 0
    db_session.expire_all()
    assert counts(db_session) == before_counts
    assert permissions(db_session) == before_permissions


def test_default_results_use_latest_batch_without_mixing_previous_runs(
    client, engine, batch_settings, selected_cases
):
    previous = submit(client, selected_cases[:1])
    process(engine, batch_settings)
    latest = submit(client, selected_cases[:1])
    assert latest["id"] != previous["id"]
    empty_latest = client.get("/api/evaluations/summary", params={"source": "scenario"}).json()
    assert empty_latest["batch_id"] == latest["id"]
    assert empty_latest["total_results"] == 0
    assert empty_latest["metrics"]["task_success"] is None
    historical = client.get(
        "/api/evaluations/results", params={"source": "scenario", "batch_id": previous["id"]}
    ).json()
    assert historical["total"] == 1
    assert historical["items"][0]["batch_id"] == previous["id"]
    assert (
        client.get("/api/evaluations/results", params={"source": "scenario"}).json()["total"] == 0
    )
    batch_list = client.get("/api/evaluations/batches", params={"limit": 1, "offset": 1}).json()
    assert batch_list["total"] == 2 and batch_list["items"][0]["id"] == previous["id"]


def test_active_lease_is_skipped_and_expired_lease_is_recovered(
    client, engine, db_session, batch_settings, selected_cases
):
    batch = submit(client, selected_cases[:1])
    batch_id = UUID(batch["id"])
    row = db_session.get(EvaluationBatch, batch_id)
    row.status = "RUNNING"
    row.started_at = utc_now()
    row.lease_owner = "stopped-worker"
    row.lease_expires_at = utc_now() + timedelta(minutes=1)
    row.attempt_count = 1
    db_session.commit()
    assert process(engine, batch_settings, "new-worker") is None
    assert (
        client.get("/api/evaluations/results", params={"source": "scenario"}).json()["total"] == 0
    )

    row.lease_expires_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    assert process(engine, batch_settings, "new-worker") == batch_id
    db_session.expire_all()
    recovered = db_session.get(EvaluationBatch, batch_id)
    assert recovered.status == "COMPLETED"
    assert recovered.completed_count == 1
    assert recovered.attempt_count == 0
    assert recovered.active_slot is recovered.lease_owner is recovered.lease_expires_at is None
    assert db_session.scalar(select(func.count()).select_from(EvaluationResult)) == 1


def test_harness_failure_does_not_create_a_success_and_retries_are_bounded(
    client, engine, db_session, batch_settings, selected_cases, monkeypatch
):
    from app.evaluation import harness

    calls = []

    def unavailable(scenario, settings):
        calls.append(scenario.id)
        raise TimeoutError("Isolated scenario process did not complete.")

    monkeypatch.setattr(harness, "run_scenario", unavailable)
    batch = submit(client, selected_cases[:1])
    batch_id = UUID(batch["id"])
    for attempt in range(1, 4):
        assert process(engine, batch_settings) == batch_id
        db_session.expire_all()
        row = db_session.get(EvaluationBatch, batch_id)
        assert row.status == "RUNNING"
        assert row.attempt_count == attempt and row.completed_count == 0
        assert row.lease_expires_at > utc_now()
        assert db_session.scalar(select(func.count()).select_from(EvaluationResult)) == 0
        row.lease_expires_at = utc_now() - timedelta(seconds=1)
        db_session.commit()

    assert process(engine, batch_settings) == batch_id
    assert len(calls) == 3
    db_session.expire_all()
    failed = db_session.get(EvaluationBatch, batch_id)
    assert failed.status == "FAILED" and failed.error
    assert failed.completed_count == 0 and failed.completed_at is not None
    assert failed.active_slot is failed.lease_owner is failed.lease_expires_at is None
    summary = client.get("/api/evaluations/summary", params={"source": "scenario"}).json()
    assert summary["total_results"] == 0 and summary["metrics"]["task_success"] is None
    assert submit(client, selected_cases[:1])["id"] != str(batch_id)


@pytest.mark.parametrize(
    "incompatibility", ["catalog_version", "missing_scenario", "attempt_limit"]
)
def test_unrecoverable_batch_is_failed_and_releases_submission_slot(
    client, engine, db_session, batch_settings, selected_cases, incompatibility
):
    batch = submit(client, selected_cases[:1])
    batch_id = UUID(batch["id"])
    row = db_session.get(EvaluationBatch, batch_id)
    if incompatibility == "catalog_version":
        row.catalog_version = "old-catalog"
    elif incompatibility == "missing_scenario":
        row.scenario_ids = ["removed-scenario"]
    else:
        row.attempt_count = 3
    db_session.commit()
    assert process(engine, batch_settings) == batch_id
    db_session.expire_all()
    failed = db_session.get(EvaluationBatch, batch_id)
    assert failed.status == "FAILED" and failed.error
    assert failed.completed_count == 0
    assert failed.active_slot is None and failed.completed_at is not None
    assert db_session.scalar(select(func.count()).select_from(EvaluationResult)) == 0
    assert submit(client, selected_cases[:1])["status"] == "PENDING"
