import os
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.agents.checkpoints import checkpoint_store
from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.db import identity_correction as correction
from app.db.base import Base
from app.db.seed import seed_database
from app.models import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
    Employee,
    EvaluationResult,
    Permission,
    Ticket,
    TicketStatus,
    ToolExecution,
    User,
)
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies
from app.schemas.tickets import TicketCreate
from app.services.approvals import decide_approval
from app.services.runs import submit_run
from app.services.tickets import create_ticket


def snapshot(engine, checkpoint_schema=None):
    serde = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=[Permission])
    result = {}
    with engine.connect() as connection:
        for schema in [None] + ([checkpoint_schema] if checkpoint_schema else []):
            for name in inspect(connection).get_table_names(schema=schema):
                table = Table(name, MetaData(), schema=schema, autoload_with=connection)
                rows = []
                for row in connection.execute(
                    select(table).order_by(*table.primary_key)
                ).mappings():
                    values = dict(row)
                    if "blob" in values and values["blob"] is not None:
                        values["blob"] = serde.loads_typed((values["type"], bytes(values["blob"])))
                    rows.append(values)
                result[f"{schema or 'application'}.{name}"] = rows
    return result


def test_recursive_replacement_preserves_checkpoint_types():
    value = {
        "employee": {"name": "Old Display Name"},
        "requested_permission": Permission.WRITE,
        "sequence": ("Reviewed Old Display Name", None, 123, True),
    }
    result = correction.replace_display_name(value, "Old Display Name", "New Display Name")
    assert result["employee"]["name"] == "New Display Name"
    assert result["requested_permission"] is Permission.WRITE
    assert result["sequence"] == ("Reviewed New Display Name", None, 123, True)
    assert value["employee"]["name"] == "Old Display Name"


def test_dry_run_apply_and_repeat_preserve_every_unrelated_value(engine, db_session):
    previous = db_session.get(Employee, "EMP001").name
    replacement = "Demo Employee Corrected"
    ticket = create_ticket(
        db_session,
        TicketCreate(employee_id="EMP001", request_text=f"{previous} needs payments write access"),
    )
    assert ticket is not None
    before = snapshot(engine)
    preview = correction.correct_employee_name(engine, "EMP001", previous, replacement)
    assert preview["employees"] == preview["users"] == preview["tickets"] == 1
    assert snapshot(engine) == before
    applied = correction.correct_employee_name(engine, "EMP001", previous, replacement, apply=True)
    assert applied == preview
    assert snapshot(engine) == correction.replace_display_name(before, previous, replacement)
    assert not any(
        correction.correct_employee_name(
            engine, "EMP001", previous, replacement, apply=True
        ).values()
    )


def test_exception_rolls_back_all_prior_table_updates(engine, db_session, monkeypatch):
    previous = db_session.get(Employee, "EMP001").name
    before = snapshot(engine)
    original = correction._rewrite_table

    def failing_rewrite(connection, table, *args, **kwargs):
        if table.name == "users":
            raise RuntimeError("Simulated write failure after employee update")
        return original(connection, table, *args, **kwargs)

    monkeypatch.setattr(correction, "_rewrite_table", failing_rewrite)
    with pytest.raises(RuntimeError, match="Simulated write failure"):
        correction.correct_employee_name(engine, "EMP001", previous, "New Display Name", apply=True)
    assert snapshot(engine) == before


@pytest.mark.parametrize("employee_id,previous", [("UNKNOWN", "Nobody"), ("EMP001", "Wrong Name")])
def test_missing_or_mismatched_employee_is_refused(engine, db_session, employee_id, previous):
    before = snapshot(engine)
    with pytest.raises(ValueError, match="missing or its current name"):
        correction.correct_employee_name(
            engine, employee_id, previous, "New Display Name", apply=True
        )
    assert snapshot(engine) == before


def test_ambiguous_previous_name_is_refused(engine, db_session):
    previous = db_session.get(Employee, "EMP001").name
    db_session.get(Employee, "EMP002").name = previous
    db_session.commit()
    before = snapshot(engine)
    with pytest.raises(ValueError, match="ambiguous"):
        correction.correct_employee_name(engine, "EMP001", previous, "New Display Name", apply=True)
    assert snapshot(engine) == before


def test_active_run_prevents_maintenance(engine, db_session):
    previous = db_session.get(Employee, "EMP001").name
    ticket = create_ticket(
        db_session,
        TicketCreate(employee_id="EMP001", request_text="Write access to payments"),
    )
    submit_run(
        db_session, ticket.id, Settings(llm_provider="demo", embedding_provider="local_hash")
    )
    before = snapshot(engine)
    with pytest.raises(ValueError, match="Active work exists"):
        correction.correct_employee_name(engine, "EMP001", previous, "New Display Name", apply=True)
    assert snapshot(engine) == before


@pytest.mark.postgres
def test_postgres_paused_checkpoint_correction_and_resume(policy_directory: Path):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Requires a disposable PostgreSQL database with pgvector")
    schema = f"identity_test_{uuid4().hex}"
    checkpoints = f"identity_checkpoints_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema},public"})
    settings = Settings(
        database_url=database_url,
        checkpoint_schema=checkpoints,
        llm_provider="demo",
        embedding_provider="local_hash",
        agent_worker_enabled=False,
    )
    try:
        with admin_engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        Base.metadata.create_all(engine)
        sessions = sessionmaker(bind=engine)
        with sessions() as session:
            seed_database(session, policy_directory)
            ingest_policies(session, LocalHashEmbeddingProvider())
            session.commit()
            previous = session.get(Employee, "EMP001").name
            ticket = create_ticket(
                session,
                TicketCreate(
                    employee_id="EMP001",
                    request_text="I need write access to the payments repository.",
                ),
            )
            run = submit_run(session, ticket.id, settings)
            run_id, ticket_id, thread_id = run.id, ticket.id, run.graph_thread_id
        assert AgentWorker(sessions, settings).run_once() == run_id
        with sessions() as session:
            assert session.get(AgentRun, run_id).status == AgentRunStatus.WAITING_FOR_APPROVAL
        with checkpoint_store(settings) as saver:
            original_values = saver.get({"configurable": {"thread_id": thread_id}})[
                "channel_values"
            ]
            original_permission_type = type(original_values["requested_permission"])
        before = snapshot(engine, checkpoints)
        replacement = "Demo Employee Corrected"
        preview = correction.correct_employee_name(
            engine, "EMP001", previous, replacement, checkpoint_schema=checkpoints
        )
        assert snapshot(engine, checkpoints) == before
        assert preview[f"{checkpoints}.checkpoint_blobs"] > 0
        assert preview[f"{checkpoints}.checkpoint_writes"] > 0
        assert (
            correction.correct_employee_name(
                engine, "EMP001", previous, replacement, checkpoint_schema=checkpoints, apply=True
            )
            == preview
        )
        assert snapshot(engine, checkpoints) == correction.replace_display_name(
            before, previous, replacement
        )
        assert not any(
            correction.correct_employee_name(
                engine, "EMP001", previous, replacement, checkpoint_schema=checkpoints, apply=True
            ).values()
        )
        with checkpoint_store(settings) as saver:
            values = saver.get({"configurable": {"thread_id": thread_id}})["channel_values"]
            assert values["employee"]["name"] == replacement
            assert values["requested_permission"] == Permission.WRITE
            assert type(values["requested_permission"]) is original_permission_type
        with sessions() as session:
            approval = session.scalar(
                select(ApprovalRequest).where(ApprovalRequest.run_id == run_id)
            )
            decide_approval(
                session,
                approval.id,
                session.get(User, approval.approver_id),
                ApprovalStatus.APPROVED,
                "Reviewed.",
            )
        assert AgentWorker(sessions, settings).run_once() == run_id
        with sessions() as session:
            assert session.get(Ticket, ticket_id).status == TicketStatus.RESOLVED
            assert session.get(AgentRun, run_id).state["verification_result"]["sufficient"]
            tools = list(
                session.scalars(
                    select(ToolExecution.tool_name).where(ToolExecution.run_id == run_id)
                )
            )
            assert tools.count("grant_repository_permission") == 1
            assert session.scalar(
                select(EvaluationResult).where(EvaluationResult.run_id == run_id)
            ).passed
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{checkpoints}" CASCADE'))
        admin_engine.dispose()
