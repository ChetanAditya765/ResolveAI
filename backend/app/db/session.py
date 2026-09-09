from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def build_engine(database_url: str) -> Engine:
    connect_args = (
        {"check_same_thread": False}
        if database_url.startswith("sqlite")
        else {"connect_timeout": 5}
    )
    db_engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    if db_engine.dialect.name == "sqlite":

        @event.listens_for(db_engine, "connect")
        def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return db_engine


engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
