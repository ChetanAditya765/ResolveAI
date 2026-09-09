from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_session
from app.main import create_app
from app.models import ConversationMessage, Ticket


def test_unavailable_database_returns_safe_error() -> None:
    app = create_app()

    def unavailable_session():
        raise OperationalError(
            "SELECT sensitive_database_detail", {}, Exception("password=do-not-expose")
        )
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = unavailable_session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/tickets")

    assert response.status_code == 503
    assert response.json()["error"]["code"]
    assert response.json()["error"]["message"]
    assert "sensitive_database_detail" not in response.text
    assert "do-not-expose" not in response.text


def test_readiness_distinguishes_schema_from_liveness(client: TestClient) -> None:
    # A create_all test database has no migration history and must not pass readiness.
    assert client.get("/api/health").status_code == 200
    response = client.get("/api/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"]


def test_readiness_reports_configured_runtime_without_secrets(db_session, monkeypatch):
    from app.api.health import MigrationContext, expected_revision

    monkeypatch.setattr(
        MigrationContext,
        "configure",
        lambda *args, **kwargs: SimpleNamespace(get_current_revision=expected_revision),
    )
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            llm_provider="openai",
            embedding_provider="openai",
            openai_api_key="private-test-api-key",
            agent_worker_enabled=False,
        )
    )

    def session_dependency():
        yield db_session

    app.dependency_overrides[get_session] = session_dependency
    with TestClient(app) as client:
        response = client.get("/api/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "connected",
        "schema_revision": expected_revision(),
        "llm_provider": "openai",
        "embedding_provider": "openai",
        "demo_auth_enabled": True,
        "agent_worker_enabled": False,
    }
    assert "private-test-api-key" not in response.text


def test_ticket_and_initial_message_are_one_transaction(
    client: TestClient, db_session: Session
) -> None:
    def fail_message_insert(_mapper, _connection, _target):
        raise OperationalError(
            "simulated message persistence failure", {}, Exception("unavailable")
        )

    event.listen(ConversationMessage, "before_insert", fail_message_insert)
    try:
        response = client.post(
            "/api/tickets",
            json={"employee_id": "EMP001", "request_text": "Read access to payments"},
        )
    finally:
        event.remove(ConversationMessage, "before_insert", fail_message_insert)

    assert response.status_code == 503
    assert db_session.scalar(select(func.count()).select_from(Ticket)) == 0
    assert db_session.scalar(select(func.count()).select_from(ConversationMessage)) == 0

    retry = client.post(
        "/api/tickets",
        json={"employee_id": "EMP001", "request_text": "Read access to payments"},
    )
    assert retry.status_code == 201
