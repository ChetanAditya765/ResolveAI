from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from sqlalchemy import text

from app.api.dependencies import DBSession
from app.core.errors import DomainError
from app.schemas.common import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@lru_cache
def expected_revision() -> str:
    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    revision = ScriptDirectory.from_config(config).get_current_head()
    if revision is None:
        raise RuntimeError("No database migration is available.")
    return revision


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.get("/ready", response_model=ReadyResponse)
def ready(session: DBSession, request: Request) -> ReadyResponse:
    session.execute(text("SELECT 1"))
    revision = MigrationContext.configure(session.connection()).get_current_revision()
    if revision != expected_revision():
        raise DomainError(503, "schema_unavailable", "Database migrations must be applied.")
    settings = request.app.state.settings
    return ReadyResponse(
        schema_revision=revision,
        llm_provider=settings.llm_provider,
        embedding_provider=settings.embedding_provider,
        demo_auth_enabled=settings.demo_auth_enabled,
        agent_worker_enabled=settings.agent_worker_enabled,
    )
