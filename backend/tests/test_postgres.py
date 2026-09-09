import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app.agents.checkpoints import checkpoint_store
from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.db.base import Base
from app.db.seed import seed_database
from app.db.session import get_session
from app.evaluation import service as evaluations
from app.main import create_app
from app.models import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
    EvaluationBatch,
    EvaluationResult,
    KnowledgeChunk,
    KnowledgeDocument,
    Ticket,
    TicketStatus,
    ToolExecution,
    User,
)
from app.rag.contracts import PolicySearch
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies
from app.rag.retrieval import search_policies
from app.schemas.tickets import TicketCreate
from app.services.approvals import decide_approval
from app.services.runs import submit_run
from app.services.tickets import create_ticket


def verify_ticket_api(connection: Connection) -> None:
    app = create_app()

    def session_dependency():
        with Session(bind=connection) as session:
            yield session

    app.dependency_overrides[get_session] = session_dependency
    with TestClient(app) as client:
        assert client.get("/api/ready").status_code == 200
        response = client.post(
            "/api/tickets",
            json={
                "employee_id": "EMP001",
                "request_text": "I need write access to the payments repository.",
            },
        )
        assert response.status_code == 201, response.text
        ticket = response.json()
        assert ticket["status"] == "OPEN"
        assert ticket["created_at"].endswith(("Z", "+00:00"))
        detail = client.get(f"/api/tickets/{ticket['id']}").json()
        assert len(detail["messages"]) == 1
        assert detail["messages"][0]["content"] == ticket["request_text"]
        assert client.get("/api/tickets").json()["total"] == 1
        assert client.delete(f"/api/tickets/{ticket['id']}").status_code == 204
        assert client.get("/api/tickets").json()["total"] == 0


def verify_postgres_graph(connection: Connection, database_url: str) -> None:
    checkpoint_schema = f"resolveai_checkpoint_test_{uuid4().hex}"
    settings = Settings(
        database_url=database_url,
        checkpoint_schema=checkpoint_schema,
        llm_provider="demo",
        embedding_provider="local_hash",
        agent_worker_enabled=False,
    )
    sessions = sessionmaker(bind=connection)
    try:
        with sessions() as session:
            provider = LocalHashEmbeddingProvider()
            ingest_policies(session, provider)
            session.commit()
            retrieved = search_policies(
                session,
                PolicySearch(
                    query="Write access requires manager approval",
                    document_slugs=["repository-access-policy"],
                    top_k=3,
                ),
                provider,
            )
            assert any(item.section == "§3. Write access" for item in retrieved.evidence)
            ticket = create_ticket(
                session,
                TicketCreate(
                    employee_id="EMP001",
                    request_text="I need write access to the payments repository.",
                ),
            )
            run = submit_run(session, ticket.id, settings)
            run_id, thread_id = run.id, run.graph_thread_id
        assert AgentWorker(sessions, settings).run_once() == run_id
        with sessions() as session:
            done = session.get(AgentRun, run_id)
            assert done.status == AgentRunStatus.WAITING_FOR_APPROVAL
            assert done.state["repository"]["name"] == "payments"
            assert done.state["current_permission"] == "read"
            assert done.state["decision"]["disposition"] == "approval_required"
            assert "Repository Access Policy §3" in done.state["decision"]["summary"]
            assert (
                len(
                    session.scalars(
                        select(ToolExecution).where(ToolExecution.run_id == run_id)
                    ).all()
                )
                == 6
            )
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
            done = session.get(AgentRun, run_id)
            assert done.status == AgentRunStatus.COMPLETED, done.state
            assert done.state["verification_result"]["sufficient"]
            assert done.state["verification_result"]["observed_permission"] == "write"
            assert session.get(Ticket, done.ticket_id).status == TicketStatus.RESOLVED
            result = session.scalar(
                select(EvaluationResult).where(EvaluationResult.run_id == run_id)
            )
            assert result is not None and result.passed
            assert result.metrics["task_success"] == 1
            assert evaluations.summarize(session, "live", None)["total_results"] == 1
            assert session.scalar(evaluations.pending_live_query()) is None
        verify_postgres_evaluations(sessions, settings)
        with checkpoint_store(settings) as saver:
            saved = saver.get({"configurable": {"thread_id": thread_id}})
            assert saved["channel_values"]["outcome"] == "granted"
            assert saved["channel_values"]["retrieved_policies"]
    finally:
        connection.rollback()
        connection.execute(text(f'DROP SCHEMA IF EXISTS "{checkpoint_schema}" CASCADE'))
        connection.commit()


def verify_postgres_evaluations(sessions, settings):
    from app.evaluation.scenarios import SCENARIOS

    scenario = next(item for item in SCENARIOS if item.expected_outcome == "granted")
    key = uuid4()
    with sessions() as session:
        manager = session.scalar(select(User).where(User.employee_id == "EMP002"))
        batch = evaluations.submit_batch(session, manager, [scenario.id], key)
        batch_id = batch.id
        assert evaluations.submit_batch(session, manager, [scenario.id], key).id == batch_id
    assert evaluations.process_next(sessions, settings, "postgres-evaluation") == batch_id
    with sessions() as session:
        assert session.get(EvaluationBatch, batch_id).status == "COMPLETED"
        summary = evaluations.summarize(session, "scenario", batch_id)
        assert summary["total_results"] == 1
        assert summary["metrics"]["task_success"] == 1
        assert summary["average_human_wait_ms"] is not None
        result = session.scalar(
            select(EvaluationResult).where(EvaluationResult.batch_id == batch_id)
        )
        assert result.run_id is None and result.snapshot["trace"]["tools"]
    assert evaluations.process_next(sessions, settings, "postgres-evaluation") is None


@pytest.mark.postgres
def test_postgres_migration_seed_and_vector_retrieval(policy_directory: Path) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database with pgvector")

    engine = create_engine(database_url)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail("TEST_DATABASE_URL must identify a PostgreSQL database")

    schema = f"resolveai_test_{uuid4().hex}"
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.attributes["version_table_schema"] = schema

    try:
        with engine.connect() as connection:
            # Extension is shared; migration cleanup deliberately preserves it.
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.commit()
            original_schema = connection.dialect.default_schema_name
            try:
                connection.execute(text(f'SET search_path TO "{schema}", public'))
                connection.commit()
                connection.dialect.default_schema_name = schema
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                tables = set(inspect(connection).get_table_names(schema=schema))
                assert tables - {"alembic_version"} == set(Base.metadata.tables)

                with Session(bind=connection) as session:
                    seed_database(session, policy_directory)
                    document = session.scalar(select(KnowledgeDocument).limit(1))
                    assert document is not None
                    relevant = [1.0] + [0.0] * 1535
                    unrelated = [0.0, 1.0] + [0.0] * 1534
                    session.add_all(
                        [
                            KnowledgeChunk(
                                document_id=document.id,
                                chunk_index=0,
                                content="Write access requires manager approval.",
                                embedding=relevant,
                                embedding_provider="test",
                                embedding_model="deterministic-1536",
                            ),
                            KnowledgeChunk(
                                document_id=document.id,
                                chunk_index=1,
                                content="Offboarding removes access for inactive employees.",
                                embedding=unrelated,
                                embedding_provider="test",
                                embedding_model="deterministic-1536",
                            ),
                        ]
                    )
                    session.commit()
                    nearest = session.scalar(
                        select(KnowledgeChunk)
                        .order_by(KnowledgeChunk.embedding.cosine_distance(relevant))
                        .limit(1)
                    )
                    assert nearest is not None
                    assert nearest.chunk_index == 0
                    assert list(nearest.embedding) == relevant

                connection.commit()
                verify_ticket_api(connection)
                connection.commit()
                verify_postgres_graph(connection, database_url)
                connection.commit()
                command.check(config)
                command.downgrade(config, "base")
                assert set(inspect(connection).get_table_names(schema=schema)) <= {
                    "alembic_version"
                }
            finally:
                connection.rollback()
                connection.dialect.default_schema_name = original_schema
                connection.execute(text("SET search_path TO public"))
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                connection.commit()
    finally:
        engine.dispose()
