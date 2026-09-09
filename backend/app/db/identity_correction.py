"""Explicit display-name maintenance for local demo identities and saved traces."""

import argparse
import json
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text, update
from sqlalchemy.engine import Connection, Engine

from app.models import Permission

# Only display-bearing fields are eligible; identifiers, timestamps, permissions,
# policy sources/hashes, evaluator versions and numerical scores are never written.
DISPLAY_COLUMNS = {
    "employees": ("name",),
    "users": ("name",),
    "tickets": ("request_text", "final_response"),
    "conversation_messages": ("content",),
    "agent_runs": ("state", "error"),
    "agent_steps": ("summary", "details"),
    "tool_executions": ("arguments", "result", "error"),
    "approval_requests": ("policy_evidence", "recommendation", "decision_comment"),
    "evaluation_results": ("snapshot", "assertions"),
    "evaluation_batches": ("error",),
}


def replace_display_name(value: Any, previous: str, replacement: str) -> Any:
    if isinstance(value, str):
        # Preserve str-derived enums in checkpoint state when their value is unchanged.
        return value.replace(previous, replacement) if previous in value else value
    if isinstance(value, dict):
        return {
            key: replace_display_name(item, previous, replacement) for key, item in value.items()
        }
    if isinstance(value, list):
        return [replace_display_name(item, previous, replacement) for item in value]
    if isinstance(value, tuple):
        return tuple(replace_display_name(item, previous, replacement) for item in value)
    return value


def _rewrite_table(
    connection: Connection,
    table: Table,
    columns: tuple[str, ...],
    previous: str,
    replacement: str,
    *,
    apply: bool,
) -> int:
    if not tuple(table.primary_key):
        raise ValueError(f"Refusing display-name maintenance without a primary key: {table.name}")
    changed = 0
    serde = JsonPlusSerializer(pickle_fallback=False, allowed_msgpack_modules=[Permission])
    for row in connection.execute(select(table)).mappings():
        values = {}
        for name in columns:
            value = row[name]
            if name == "blob":
                if value is None or previous.encode("utf-8") not in bytes(value):
                    continue
                original = serde.loads_typed((row["type"], bytes(value)))
                corrected = replace_display_name(original, previous, replacement)
                if original == corrected:
                    raise ValueError("A checkpoint contains an unsupported display-name value")
                blob_type, corrected_blob = serde.dumps_typed(corrected)
                if serde.loads_typed((blob_type, corrected_blob)) != corrected:
                    raise ValueError("Checkpoint serialization did not preserve its content")
                values.update(type=blob_type, blob=corrected_blob)
            else:
                corrected = replace_display_name(value, previous, replacement)
                if corrected != value:
                    values[name] = corrected
        if values:
            changed += 1
            if apply:
                connection.execute(
                    update(table)
                    .where(*(column == row[column.name] for column in table.primary_key))
                    .values(**values)
                )
    return changed


def correct_employee_name(
    engine: Engine,
    employee_id: str,
    previous: str,
    replacement: str,
    *,
    checkpoint_schema: str = "resolveai_checkpoints",
    apply: bool = False,
) -> dict[str, int]:
    """Atomically correct names; callers must stop the API/worker before applying."""
    if not previous.strip() or not replacement.strip() or len(replacement) > 120:
        raise ValueError("Names must be nonblank and the new name must be at most 120 characters")
    if previous == replacement:
        return {}
    with engine.begin() as connection:
        metadata = MetaData()
        tables = {
            name: (Table(name, metadata, autoload_with=connection), columns)
            for name, columns in DISPLAY_COLUMNS.items()
        }
        if connection.dialect.name == "postgresql":
            inspector = inspect(connection)
            if inspector.has_schema(checkpoint_schema):
                for name, columns in (
                    ("checkpoints", ("checkpoint", "metadata")),
                    ("checkpoint_blobs", ("blob",)),
                    ("checkpoint_writes", ("blob",)),
                ):
                    if inspector.has_table(name, schema=checkpoint_schema):
                        table = Table(
                            name, metadata, schema=checkpoint_schema, autoload_with=connection
                        )
                        tables[f"{checkpoint_schema}.{name}"] = (table, columns)
            preparer = connection.dialect.identifier_preparer
            names = ", ".join(preparer.format_table(table) for table, _ in tables.values())
            connection.execute(text(f"LOCK TABLE {names} IN SHARE ROW EXCLUSIVE MODE"))
        employees = tables["employees"][0]
        employee = (
            connection.execute(select(employees).where(employees.c.id == employee_id))
            .mappings()
            .one_or_none()
        )
        if employee is None or employee["name"] not in (previous, replacement):
            raise ValueError("Employee is missing or its current name does not match")
        if connection.scalar(
            select(employees.c.id).where(
                employees.c.id != employee_id, employees.c.name == previous
            )
        ):
            raise ValueError("Another employee shares the previous name; correction is ambiguous")
        for name, statuses in (
            ("agent_runs", ("PENDING", "RUNNING")),
            ("evaluation_batches", ("PENDING", "RUNNING")),
        ):
            table = tables[name][0]
            if connection.scalar(select(table.c.id).where(table.c.status.in_(statuses)).limit(1)):
                raise ValueError("Active work exists; let it settle and stop the API/worker first")
        return {
            name: _rewrite_table(connection, table, columns, previous, replacement, apply=apply)
            for name, (table, columns) in tables.items()
        }


def main() -> None:
    from app.core.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("employee_id")
    parser.add_argument("previous_name")
    parser.add_argument("new_name")
    parser.add_argument("--apply", action="store_true", help="Commit; default only reports changes")
    args = parser.parse_args()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        counts = correct_employee_name(
            engine,
            args.employee_id,
            args.previous_name,
            args.new_name,
            checkpoint_schema=settings.checkpoint_schema,
            apply=args.apply,
        )
        print(json.dumps({"applied": args.apply, "changed_rows": counts}, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
