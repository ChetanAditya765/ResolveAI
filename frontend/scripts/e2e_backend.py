"""Serve a seeded, isolated offline backend for Playwright's webServer lifecycle."""

import argparse
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8011)
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error("--port must be between 1024 and 65535")

    with tempfile.TemporaryDirectory(prefix="resolveai-browser-") as directory:
        storage = Path(directory)
        os.environ.update(
            APP_ENV="test",
            LOG_LEVEL="WARNING",
            DEMO_AUTH_ENABLED="true",
            SESSION_SIGNING_KEY=secrets.token_urlsafe(48),
            AGENT_WORKER_ENABLED="true",
            AGENT_POLL_SECONDS="0.1",
            LLM_PROVIDER="demo",
            EMBEDDING_PROVIDER="local_hash",
            RAG_TOP_K="8",
            RAG_MIN_SCORE="0.1",
            DATABASE_URL=f"sqlite+pysqlite:///{(storage / 'test.db').as_posix()}",
            CHECKPOINT_SQLITE_PATH=str(storage / "checkpoints.sqlite"),
        )
        for command in (("alembic", "upgrade", "head"), ("app.db.seed",)):
            subprocess.run(
                [sys.executable, "-m", *command],
                cwd=BACKEND,
                env=os.environ,
                check=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

        sys.path.insert(0, str(BACKEND))
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=arguments.port,
            access_log=False,
            log_level="warning",
        )


if __name__ == "__main__":
    main()
