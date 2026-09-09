import argparse
import json
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.rag.contracts import RagError
from app.rag.embeddings import create_embedding_provider
from app.rag.ingestion import ingest_policies


def main() -> int:
    parser = argparse.ArgumentParser(description="Index the seeded company policy documents.")
    parser.add_argument("--force", action="store_true", help="Replace every policy embedding.")
    arguments = parser.parse_args()
    try:
        provider = create_embedding_provider(get_settings())
        with SessionLocal.begin() as session:
            counts = ingest_policies(session, provider, force=arguments.force)
    except RagError as exc:
        print(
            json.dumps({"status": "failed", "code": exc.code, "message": str(exc)}), file=sys.stderr
        )
        return 1
    except SQLAlchemyError:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": "database_error",
                    "message": "Policy ingestion failed. Check connectivity and migrations.",
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": "indexed",
                "provider": provider.provider_name,
                "model": provider.model_name,
                **counts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
