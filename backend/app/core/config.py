from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://resolveai:resolveai_local@localhost:5432/resolveai"
    cors_origins: list[str] = ["http://localhost:3000"]
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    demo_auth_enabled: bool = True
    session_signing_key: SecretStr = SecretStr("resolveai-local-demo-key-change-before-hosting")
    session_ttl_seconds: int = Field(default=28800, ge=300, le=86400)
    policy_directory: Path = PROJECT_ROOT / "policies"
    llm_provider: Literal["demo", "openai"] = "demo"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    llm_timeout_seconds: float = Field(default=20, ge=1, le=60)
    embedding_provider: Literal["local_hash", "openai"] = "local_hash"
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1, max_length=120)
    embedding_dimensions: int = Field(default=1536, ge=1536, le=1536)
    embedding_timeout_seconds: float = Field(default=20, ge=1, le=60)
    rag_top_k: int = Field(default=8, ge=1, le=20)
    rag_min_score: float = Field(default=0.1, ge=0, le=1)
    agent_worker_enabled: bool = True
    agent_poll_seconds: float = Field(default=1, ge=0.1, le=30)
    agent_lease_seconds: int = Field(default=180, ge=120, le=900)
    agent_max_attempts: int = Field(default=3, ge=1, le=5)
    checkpoint_schema: str = Field(
        default="resolveai_checkpoints", pattern=r"^[a-z][a-z0-9_]{0,62}$"
    )
    checkpoint_sqlite_path: Path = PROJECT_ROOT / ".local" / "checkpoints.sqlite"

    @model_validator(mode="after")
    def validate_demo_auth(self):
        if len(self.session_signing_key.get_secret_value()) < 32:
            raise ValueError("SESSION_SIGNING_KEY must contain at least 32 characters.")
        if self.app_env == "production" and self.demo_auth_enabled:
            raise ValueError("Demo identity selection must be disabled in production.")
        if self.app_env == "production" and (
            self.session_signing_key.get_secret_value()
            == "resolveai-local-demo-key-change-before-hosting"
        ):
            raise ValueError("Production requires a configured SESSION_SIGNING_KEY.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
