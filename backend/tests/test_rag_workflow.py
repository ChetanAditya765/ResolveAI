import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from app.agents.worker import AgentWorker
from app.core.config import Settings
from app.db.base import utc_now
from app.db.seed import seed_database
from app.models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    KnowledgeChunk,
    KnowledgeDocument,
    Repository,
    RepositoryPermission,
    Ticket,
    TicketStatus,
    ToolExecution,
    ToolExecutionStatus,
)
from app.rag.contracts import RagError
from app.rag.embeddings import LocalHashEmbeddingProvider
from app.rag.ingestion import ingest_policies
from app.schemas.tickets import TicketCreate
from app.services import runs
from app.services.tickets import create_ticket


@pytest.fixture
def indexed_settings(db_session, tmp_path: Path) -> Settings:
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    db_session.commit()
    return Settings(
        database_url="sqlite+pysqlite://",
        checkpoint_sqlite_path=tmp_path / "checkpoints.sqlite",
        agent_worker_enabled=False,
    )


def submit(session, settings, request="Write access to payments", employee_id="EMP001"):
    ticket = create_ticket(session, TicketCreate(employee_id=employee_id, request_text=request))
    run = runs.submit_run(session, ticket.id, settings)
    # Version 2 remains supported for runs created before approval execution shipped.
    run.state = {**run.state, "workflow_version": 2}
    session.commit()
    return run


@pytest.mark.parametrize(
    "request_text,employee_id,disposition,citation",
    [
        ("Read access to payments", "EMP001", "no_change", "Least Privilege Policy §2"),
        ("Read access to payments", "EMP004", "approval_required", "Repository Access Policy §4"),
        ("Write access to payments", "EMP004", "approval_required", "Repository Access Policy §3"),
        ("Admin access to payments", "EMP001", "escalate", "Repository Access Policy §5"),
    ],
)
def test_actual_retrieval_drives_policy_decisions(
    engine, db_session, indexed_settings, request_text, employee_id, disposition, citation
):
    run = submit(db_session, indexed_settings, request_text, employee_id)
    before = {p.id: p.permission for p in db_session.scalars(select(RepositoryPermission))}
    AgentWorker(sessionmaker(bind=engine), indexed_settings).run_once()
    db_session.expire_all()
    finished = db_session.get(AgentRun, run.id)
    assert finished.status == AgentRunStatus.COMPLETED
    assert finished.state["decision"]["disposition"] == disposition
    assert citation in finished.state["decision"]["summary"]
    evidence_ids = {p["chunk_id"] for p in finished.state["retrieved_policies"]}
    assert set(finished.state["decision"]["policy_chunk_ids"]) <= evidence_ids
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.ESCALATED
    assert before == {p.id: p.permission for p in db_session.scalars(select(RepositoryPermission))}


def test_same_department_read_is_permitted_but_not_executed_in_phase_three(
    engine, db_session, indexed_settings
):
    payments = db_session.scalar(select(Repository).where(Repository.name == "payments"))
    db_session.execute(
        delete(RepositoryPermission).where(
            RepositoryPermission.employee_id == "EMP001",
            RepositoryPermission.repository_id == payments.id,
        )
    )
    db_session.commit()
    run = submit(db_session, indexed_settings, "Read access to payments")
    AgentWorker(sessionmaker(bind=engine), indexed_settings).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.state["decision"]["disposition"] == "grant"
    assert "Repository Access Policy §2" in done.state["final_response"]
    assert "No access was changed" in done.state["final_response"]
    assert (
        "grant_repository_permission"
        not in db_session.scalars(
            select(ToolExecution.tool_name).where(ToolExecution.run_id == run.id)
        ).all()
    )


@pytest.mark.parametrize("failure", ["empty_index", "timeout", "model_mismatch"])
def test_retrieval_failure_is_audited_and_never_authorizes(
    engine, db_session, indexed_settings, failure
):
    class FailingEmbedding(LocalHashEmbeddingProvider):
        def embed(self, texts):
            raise RagError("embedding_timeout", "The embedding request timed out.")

    provider = FailingEmbedding() if failure == "timeout" else None
    if failure == "empty_index":
        db_session.execute(delete(KnowledgeChunk))
        db_session.commit()
    elif failure == "model_mismatch":
        chunk = db_session.scalar(select(KnowledgeChunk).limit(1))
        chunk.embedding_model = "different-model"
        db_session.commit()
    run = submit(db_session, indexed_settings)
    AgentWorker(sessionmaker(bind=engine), indexed_settings, embedding_provider=provider).run_once()
    db_session.expire_all()
    finished = db_session.get(AgentRun, run.id)
    assert finished.status == AgentRunStatus.FAILED
    assert finished.state["error_code"] == "policy_retrieval_failed"
    assert finished.state["decision"] is None
    assert finished.state["retrieved_policies"] == []
    assert db_session.get(Ticket, run.ticket_id).status == TicketStatus.ESCALATED
    tool = db_session.scalar(
        select(ToolExecution).where(
            ToolExecution.run_id == run.id, ToolExecution.tool_name == "search_policies"
        )
    )
    assert tool.status == ToolExecutionStatus.FAILED
    assert tool.arguments["document_slugs"] == ["repository-access-policy"]


def test_reindexed_changed_policy_cannot_activate_old_authorization_rules(
    engine, db_session, indexed_settings
):
    document = db_session.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.slug == "repository-access-policy")
    )
    document.content = document.content.replace("Version: 1.0", "Version: 1.1")
    document.version = "1.1"
    document.content_hash = hashlib.sha256(document.content.encode()).hexdigest()
    db_session.commit()
    ingest_policies(db_session, LocalHashEmbeddingProvider())
    db_session.commit()
    run = submit(db_session, indexed_settings)
    AgentWorker(sessionmaker(bind=engine), indexed_settings).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.state["retrieved_policies"]
    assert done.state["decision"]["disposition"] == "escalate"
    assert done.state["decision"]["policy_chunk_ids"] == []
    assert "unsupported" in done.state["decision"]["summary"]


def test_recovery_uses_original_retrieval_configuration(
    engine, db_session, indexed_settings, monkeypatch
):
    run = submit(db_session, indexed_settings)
    original = runs.finish_step

    def crash_before_retrieval(*args, **kwargs):
        if args[4].current_node == "CHECK_CURRENT_ACCESS":
            raise RuntimeError("Process stopped before the policy step")
        return original(*args, **kwargs)

    monkeypatch.setattr(runs, "finish_step", crash_before_retrieval)
    AgentWorker(sessionmaker(bind=engine), indexed_settings).run_once()
    monkeypatch.setattr(runs, "finish_step", original)
    db_session.expire_all()
    recovering = db_session.get(AgentRun, run.id)
    recovering.lease_expires_at = utc_now() - timedelta(seconds=1)
    db_session.commit()
    changed = indexed_settings.model_copy(
        update={"embedding_provider": "openai", "openai_api_key": None, "rag_min_score": 1}
    )
    AgentWorker(sessionmaker(bind=engine), changed).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.status == AgentRunStatus.COMPLETED
    assert done.state["decision"]["disposition"] == "approval_required"
    assert done.state["retrieval_config"]["provider"] == "local_hash"
    assert db_session.scalar(select(func.count()).select_from(ToolExecution)) == 5


def test_old_phase_two_runs_recover_with_original_node_sequence(
    engine, db_session, indexed_settings
):
    run = submit(db_session, indexed_settings)
    legacy = dict(run.state)
    legacy.pop("workflow_version")
    legacy.pop("retrieval_config")
    run.state = legacy
    db_session.commit()
    AgentWorker(sessionmaker(bind=engine), indexed_settings).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.status == AgentRunStatus.COMPLETED
    assert done.state["decision"]["disposition"] == "escalate"
    assert done.state["retrieved_policies"] == []
    assert db_session.scalar(select(func.count()).select_from(AgentStep)) == 7


def test_changed_local_embedding_implementation_cannot_silently_replace_run_identity(
    engine, db_session, indexed_settings
):
    run = submit(db_session, indexed_settings)
    saved = dict(run.state)
    saved["retrieval_config"] = {**saved["retrieval_config"], "model": "older-lexical-version"}
    run.state = saved
    db_session.commit()
    AgentWorker(sessionmaker(bind=engine), indexed_settings).run_once()
    db_session.expire_all()
    done = db_session.get(AgentRun, run.id)
    assert done.status == AgentRunStatus.FAILED
    assert done.state["decision"] is None
    assert "different embedding configuration" in done.state["final_response"]


def test_seed_indexes_automatically_and_failed_embeddings_roll_back_seed(engine, policy_directory):
    class FailingEmbedding(LocalHashEmbeddingProvider):
        def embed(self, texts):
            raise RagError("embedding_timeout", "Embedding failed.")

    with sessionmaker(bind=engine)() as session:
        with pytest.raises(RagError):
            seed_database(session, policy_directory, FailingEmbedding())
        assert session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0
        seed_database(session, policy_directory, LocalHashEmbeddingProvider())
        original_ids = set(session.scalars(select(KnowledgeChunk.id)))
        assert len(original_ids) >= 30
        seed_database(session, policy_directory, LocalHashEmbeddingProvider())
        assert set(session.scalars(select(KnowledgeChunk.id))) == original_ids


def test_policy_api_lists_sources_and_returns_real_retrieval(client, indexed_settings):
    listed = client.get("/api/policies?limit=2&offset=0")
    assert listed.status_code == 200
    assert listed.json()["total"] == 8
    assert len(listed.json()["items"]) == 2
    document = listed.json()["items"][0]
    assert "content" not in document
    detail = client.get(f"/api/policies/{document['id']}")
    assert detail.status_code == 200
    assert detail.json()["content"].startswith("# " + document["title"])
    assert client.get(f"/api/policies/{uuid4()}").status_code == 404
    search = client.post(
        "/api/policies/search", json={"query": "write access manager approval", "top_k": 3}
    )
    assert search.status_code == 200
    assert search.json()["embedding_provider"] == "local_hash"
    assert 1 <= len(search.json()["evidence"]) <= 3
    assert client.post("/api/policies/search", json={"query": "", "top_k": 0}).status_code == 422


def test_policy_api_without_index_reports_unavailable(client):
    response = client.post("/api/policies/search", json={"query": "write access"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "policy_index_unavailable"
