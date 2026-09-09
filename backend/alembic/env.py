from logging.config import fileConfig

from sqlalchemy import create_engine, pool

import app.models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
database_url = get_settings().database_url


def include_name(name: str | None, type_: str, _parent_names: dict) -> bool:
    # With a custom version schema, default-schema reflection may still see this table.
    return not (type_ == "table" and name == "alembic_version")


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided_connection = config.attributes.get("connection")
    if provided_connection is not None:
        context.configure(
            connection=provided_connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=config.attributes.get("version_table_schema"),
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
