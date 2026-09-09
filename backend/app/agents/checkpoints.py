import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from app.core.config import Settings, get_settings
from app.models import Permission


@contextmanager
def checkpoint_store(settings: Settings) -> Iterator[BaseCheckpointSaver]:
    # Classification deltas retain this enum; other custom constructors stay blocked.
    serde = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=[Permission])
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        path = settings.checkpoint_sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, check_same_thread=False)) as connection:
            saver = SqliteSaver(connection, serde=serde)
            saver.setup()
            yield saver
        return
    with psycopg.connect(
        url.set(drivername="postgresql").render_as_string(hide_password=False),
        autocommit=True,
        row_factory=dict_row,
        prepare_threshold=0,
        connect_timeout=5,
    ) as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(settings.checkpoint_schema)
            )
        )
        connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(settings.checkpoint_schema))
        )
        saver = PostgresSaver(connection, serde=serde)
        saver.setup()
        yield saver


def main() -> None:
    with checkpoint_store(get_settings()):
        print("LangGraph checkpoint storage initialized.")


if __name__ == "__main__":
    main()
