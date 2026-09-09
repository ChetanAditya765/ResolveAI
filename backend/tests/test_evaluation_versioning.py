from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from sqlalchemy import MetaData, create_engine, func, select
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.db.base import utc_now
from app.evaluation import service
from app.evaluation.contracts import CATALOG_VERSION, EVALUATOR_VERSION
from app.evaluation.scenarios import SCENARIOS
from app.evaluation.trace import collect_trace
from app.models import AgentRun, EvaluationBatch, EvaluationResult, User
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies
from app.schemas.tickets import TicketCreate
from app.services.runs import submit_run
from app.services.tickets import create_ticket

LEGACY_VERSION = "deterministic-v1"


def make_batch(session, *, active=False):
    actor = session.scalar(select(User).where(User.employee_id == "EMP002"))
    batch = EvaluationBatch(
        requested_by_id=actor.id,
        submission_key=uuid4(),
        status="RUNNING" if active else "COMPLETED",
        active_slot=1 if active else None,
        scenario_ids=[case.id for case in SCENARIOS[:2]],
        catalog_version=CATALOG_VERSION,
        evaluator_version=LEGACY_VERSION,
        total_count=2,
        completed_count=1,
        lease_owner="previous-application" if active else None,
        lease_expires_at=utc_now() - timedelta(seconds=1) if active else None,
    )
    session.add(batch)
    session.flush()
    result = EvaluationResult(
        source="scenario",
        batch_id=batch.id,
        scenario_id=batch.scenario_ids[0],
        evaluator_version=LEGACY_VERSION,
        metrics={"task_success": 0.0, "policy_compliance": 0.25},
        assertions=[],
        passed=False,
        latency_ms=120,
        snapshot={"trace": {"run": {}, "ticket": {}}, "human_wait_ms": 240},
    )
    session.add(result)
    session.commit()
    return batch, result


def test_historical_batch_results_and_summary_use_recorded_scorer(client, db_session):
    batch, old_result = make_batch(db_session)
    # A differently versioned record must not affect this batch's historical aggregate.
    db_session.add(
        EvaluationResult(
            source="scenario",
            batch_id=batch.id,
            scenario_id=batch.scenario_ids[0],
            evaluator_version=EVALUATOR_VERSION,
            metrics={"task_success": 1.0},
            assertions=[],
            passed=True,
            snapshot={},
        )
    )
    db_session.commit()
    for parameters in ({"source": "scenario"}, {"source": "scenario", "batch_id": str(batch.id)}):
        response = client.get("/api/evaluations/results", params=parameters)
        assert response.status_code == 200, response.text
        results = response.json()
        assert results["total"] == 1
        assert results["items"][0]["id"] == str(old_result.id)
        assert results["items"][0]["evaluator_version"] == LEGACY_VERSION
        response = client.get("/api/evaluations/summary", params=parameters)
        assert response.status_code == 200, response.text
        summary = response.json()
        assert summary["evaluator_version"] == LEGACY_VERSION
        assert summary["total_results"] == 1
        assert summary["passed_results"] == 0
        assert summary["metrics"]["task_success"] == 0
        assert summary["metrics"]["policy_compliance"] == 0.25
        assert summary["average_latency_ms"] == 120
        assert summary["average_human_wait_ms"] == 240
    detail = client.get(f"/api/evaluations/results/{old_result.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["evaluator_version"] == LEGACY_VERSION
    batch_detail = client.get(f"/api/evaluations/batches/{batch.id}").json()
    assert batch_detail["evaluator_version"] == LEGACY_VERSION
    assert client.get("/api/evaluations/batches").json()["items"][0] == batch_detail


def test_incompatible_active_batch_stops_without_mixing_scores(
    engine, db_session, policy_directory, monkeypatch
):
    from app.evaluation import harness

    batch, old_result = make_batch(db_session, active=True)
    saved_snapshot = old_result.snapshot

    def forbidden(*args):
        raise AssertionError("An incompatible batch must not execute any scenario.")

    monkeypatch.setattr(harness, "run_scenario", forbidden)
    settings = Settings(database_url="sqlite+pysqlite://", policy_directory=policy_directory)
    assert service.process_next(sessionmaker(bind=engine), settings, "new-worker") == batch.id
    db_session.expire_all()
    failed = db_session.get(EvaluationBatch, batch.id)
    assert failed.status == "FAILED"
    assert "evaluator version" in failed.error
    assert failed.completed_count == 1
    assert failed.completed_at is not None
    assert failed.active_slot is failed.lease_owner is failed.lease_expires_at is None
    assert failed.evaluator_version == LEGACY_VERSION
    assert db_session.scalar(select(func.count()).select_from(EvaluationResult)) == 1
    assert db_session.get(EvaluationResult, old_result.id).snapshot == saved_snapshot
    actor = db_session.get(User, failed.requested_by_id)
    # Retrying the original submission retains its identity and historical scorer version.
    retry = service.submit_batch(db_session, actor, failed.scenario_ids, failed.submission_key)
    assert retry.id == failed.id and retry.evaluator_version == LEGACY_VERSION
    replacement = service.submit_batch(db_session, actor, failed.scenario_ids, uuid4())
    assert replacement.status == "PENDING"
    assert replacement.evaluator_version == EVALUATOR_VERSION == "deterministic-v2"
    assert replacement.catalog_version == CATALOG_VERSION == "access-v1"


def test_live_backfill_adds_v2_and_preserves_v1(client, engine, db_session, tmp_path):
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    settings = Settings(
        database_url="sqlite+pysqlite://",
        checkpoint_sqlite_path=tmp_path / "checkpoints.sqlite",
        llm_provider="demo",
        embedding_provider="local_hash",
    )
    ticket = create_ticket(
        db_session,
        TicketCreate(employee_id="EMP007", request_text="I need read access to payments."),
    )
    run = submit_run(db_session, ticket.id, settings)
    sessions = sessionmaker(bind=engine)
    AgentWorker(sessions, settings, evaluate_completed=False).run_once()
    db_session.expire_all()
    assert db_session.get(AgentRun, run.id).state["outcome"] == "granted"
    original = EvaluationResult(
        source="live",
        scenario_id="live_run",
        run_id=run.id,
        evaluator_version=LEGACY_VERSION,
        metrics={"task_success": 0.0},
        assertions=[],
        passed=False,
        snapshot={"trace": collect_trace(db_session, run.id).model_dump(mode="json")},
    )
    db_session.add(original)
    db_session.commit()
    assert db_session.scalar(service.pending_live_query()) == run.id
    service.backfill_one(sessions)
    service.backfill_one(sessions)
    db_session.expire_all()
    saved = db_session.scalars(
        select(EvaluationResult).where(EvaluationResult.run_id == run.id)
    ).all()
    assert len(saved) == 2
    by_version = {item.evaluator_version: item for item in saved}
    assert by_version[LEGACY_VERSION].id == original.id
    assert by_version[LEGACY_VERSION].metrics == {"task_success": 0.0}
    assert by_version[EVALUATOR_VERSION].passed, by_version[EVALUATOR_VERSION].assertions
    assert by_version[EVALUATOR_VERSION].metrics["task_success"] == 1
    assert db_session.scalar(service.pending_live_query()) is None
    assert service.evaluate_run(db_session, run.id).id == by_version[EVALUATOR_VERSION].id
    results = client.get("/api/evaluations/results", params={"run_id": str(run.id)}).json()
    assert results["total"] == 1
    assert results["items"][0]["evaluator_version"] == EVALUATOR_VERSION
    summary = client.get("/api/evaluations/summary").json()
    assert summary["evaluator_version"] == EVALUATOR_VERSION
    assert summary["total_results"] == 1 and summary["metrics"]["task_success"] == 1
    historical = client.get(f"/api/evaluations/results/{original.id}").json()
    assert historical["evaluator_version"] == LEGACY_VERSION
    assert historical["metrics"]["task_success"] == 0


def test_migration_backfills_existing_batches_and_defaults_new_inserts_to_v2(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'versions.sqlite').as_posix()}")
    actor_id, batch_id, result_id = (uuid4().hex for _ in range(3))
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0004")
            metadata = MetaData()
            metadata.reflect(connection)
            connection.execute(
                metadata.tables["users"]
                .insert()
                .values(id=actor_id, name="Manager", email="manager@example.test", role="manager")
            )
            payload = {
                "requested_by_id": actor_id,
                "submission_key": uuid4().hex,
                "status": "COMPLETED",
                "active_slot": None,
                "scenario_ids": [SCENARIOS[0].id],
                "catalog_version": CATALOG_VERSION,
                "total_count": 1,
                "completed_count": 1,
            }
            connection.execute(
                metadata.tables["evaluation_batches"].insert().values(id=batch_id, **payload)
            )
            connection.execute(
                metadata.tables["evaluation_results"]
                .insert()
                .values(
                    id=result_id,
                    batch_id=batch_id,
                    source="scenario",
                    scenario_id=SCENARIOS[0].id,
                    evaluator_version=LEGACY_VERSION,
                    metrics={"task_success": 0},
                    assertions=[],
                    passed=False,
                    snapshot={"trace": {"run": {}, "ticket": {}}},
                )
            )
            connection.commit()
            command.upgrade(config, "head")
            metadata = MetaData()
            metadata.reflect(connection)
            batches = metadata.tables["evaluation_batches"]
            assert (
                connection.scalar(
                    select(batches.c.evaluator_version).where(batches.c.id == batch_id)
                )
                == LEGACY_VERSION
            )
            new_id = uuid4().hex
            connection.execute(
                batches.insert().values(id=new_id, **{**payload, "submission_key": uuid4().hex})
            )
            assert (
                connection.scalar(select(batches.c.evaluator_version).where(batches.c.id == new_id))
                == EVALUATOR_VERSION
            )
            results = metadata.tables["evaluation_results"]
            historical = (
                connection.execute(select(results).where(results.c.id == result_id))
                .mappings()
                .one()
            )
            assert historical["evaluator_version"] == LEGACY_VERSION
            assert historical["metrics"] == {"task_success": 0}
            assert historical["snapshot"] == {"trace": {"run": {}, "ticket": {}}}
            connection.commit()
    finally:
        engine.dispose()
