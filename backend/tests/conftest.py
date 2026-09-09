from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.seed import seed_database
from app.db.session import get_session
from app.main import create_app


@pytest.fixture(autouse=True)
def disable_default_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "agent_worker_enabled", False)
    monkeypatch.setattr(get_settings(), "llm_provider", "demo")
    monkeypatch.setattr(get_settings(), "embedding_provider", "local_hash")
    monkeypatch.setenv("LLM_PROVIDER", "demo")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local_hash")


@pytest.fixture
def engine() -> Iterator[Engine]:
    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(test_engine, "connect")
    def enable_foreign_keys(connection, _connection_record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def policy_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "policies"


@pytest.fixture
def db_session(engine: Engine, policy_directory: Path) -> Iterator[Session]:
    with Session(engine) as session:
        seed_database(session, policy_directory)
        yield session


@pytest.fixture
def client(engine: Engine, db_session: Session) -> Iterator[TestClient]:
    session_factory = sessionmaker(bind=engine)

    def override_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
