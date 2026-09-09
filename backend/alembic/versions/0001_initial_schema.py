"""Create the ResolveAI domain, execution audit, and policy schema.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def _json() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "employees",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("department", sa.String(80), nullable=False),
        sa.Column("role", sa.String(120), nullable=False),
        sa.Column("manager_id", sa.String(32), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("manager_id IS NULL OR manager_id <> id", name="not_own_manager"),
        sa.ForeignKeyConstraint(["manager_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_employees_department", "employees", ["department"])
    op.create_index("ix_employees_manager_id", "employees", ["manager_id"])

    op.create_table(
        "users",
        _id(),
        sa.Column("employee_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", _enum("user_role", "employee", "manager", "admin"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "repositories",
        _id(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("owning_department", sa.String(80), nullable=False),
        sa.Column(
            "sensitivity_level",
            _enum("repository_sensitivity", "internal", "confidential", "restricted"),
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_repositories_owning_department", "repositories", ["owning_department"])

    op.create_table(
        "repository_permissions",
        _id(),
        sa.Column("employee_id", sa.String(32), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column(
            "permission",
            _enum("permission_level", "none", "read", "write", "admin"),
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "employee_id", "repository_id", name="uq_permission_employee_repository"
        ),
    )
    op.create_index(
        "ix_repository_permissions_employee_id", "repository_permissions", ["employee_id"]
    )
    op.create_index(
        "ix_repository_permissions_repository_id", "repository_permissions", ["repository_id"]
    )

    op.create_table(
        "tickets",
        _id(),
        sa.Column("employee_id", sa.String(32), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "ticket_status",
                "OPEN",
                "PROCESSING",
                "WAITING_FOR_APPROVAL",
                "RESOLVED",
                "ESCALATED",
                "FAILED",
            ),
            server_default="OPEN",
            nullable=False,
        ),
        _created_at(),
        _updated_at(),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_response", sa.Text(), nullable=True),
        sa.CheckConstraint("length(trim(request_text)) > 0", name="request_not_blank"),
        sa.CheckConstraint(
            "status <> 'RESOLVED' OR resolved_at IS NOT NULL", name="resolved_has_timestamp"
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tickets_employee_id", "tickets", ["employee_id"])
    op.create_index("ix_tickets_status", "tickets", ["status"])

    op.create_table(
        "conversation_messages",
        _id(),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role", _enum("conversation_role", "user", "assistant", "system"), nullable=False
        ),
        sa.Column("content", sa.Text(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_messages_ticket_id", "conversation_messages", ["ticket_id"])

    op.create_table(
        "agent_runs",
        _id(),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "agent_run_status",
                "PENDING",
                "RUNNING",
                "WAITING_FOR_APPROVAL",
                "COMPLETED",
                "FAILED",
            ),
            nullable=False,
        ),
        sa.Column("state", _json(), nullable=False),
        sa.Column("graph_thread_id", sa.String(80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("graph_thread_id"),
    )
    op.create_index("ix_agent_runs_ticket_id", "agent_runs", ["ticket_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index(
        "uq_agent_runs_active_ticket",
        "agent_runs",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING', 'WAITING_FOR_APPROVAL')"),
        sqlite_where=sa.text("status IN ('PENDING', 'RUNNING', 'WAITING_FOR_APPROVAL')"),
    )

    op.create_table(
        "agent_steps",
        _id(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node", sa.String(80), nullable=False),
        sa.Column(
            "status",
            _enum("agent_step_status", "RUNNING", "COMPLETED", "WAITING", "FAILED"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", _json(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("sequence >= 0", name="nonnegative_sequence"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="nonnegative_latency"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_step_run_sequence"),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])

    op.create_table(
        "tool_executions",
        _id(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments", _json(), nullable=False),
        sa.Column("result", _json(), nullable=True),
        sa.Column(
            "status",
            _enum("tool_execution_status", "RUNNING", "SUCCEEDED", "FAILED"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="nonnegative_latency"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["step_id"], ["agent_steps.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_tool_executions_run_id", "tool_executions", ["run_id"])
    op.create_index("ix_tool_executions_step_id", "tool_executions", ["step_id"])
    op.create_index("ix_tool_executions_tool_name", "tool_executions", ["tool_name"])

    op.create_table(
        "approval_requests",
        _id(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.String(32), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column(
            "permission",
            _enum("approval_permission_level", "none", "read", "write", "admin"),
            nullable=False,
        ),
        sa.Column("approver_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", _enum("approval_status", "PENDING", "APPROVED", "REJECTED"), nullable=False
        ),
        sa.Column("policy_evidence", _json(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(status = 'PENDING' AND decided_at IS NULL AND decided_by_id IS NULL) OR "
            "(status <> 'PENDING' AND decided_at IS NOT NULL AND decided_by_id IS NOT NULL)",
            name="decision_audit_consistent",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approver_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approval_requests_run_id", "approval_requests", ["run_id"])
    op.create_index("ix_approval_requests_ticket_id", "approval_requests", ["ticket_id"])
    op.create_index("ix_approval_requests_approver_id", "approval_requests", ["approver_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])

    op.create_table(
        "knowledge_documents",
        _id(),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", _json(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "knowledge_chunks",
        _id(),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536).with_variant(sa.JSON(), "sqlite"), nullable=True),
        sa.Column("embedding_provider", sa.String(60), nullable=True),
        sa.Column("embedding_model", sa.String(120), nullable=True),
        sa.Column("metadata", _json(), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="nonnegative_chunk_index"),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_document_index"),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])

    op.create_table(
        "evaluation_results",
        _id(),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("scenario_id", sa.String(120), nullable=False),
        sa.Column("evaluator_version", sa.String(32), nullable=False),
        sa.Column("metrics", _json(), nullable=False),
        sa.Column("assertions", _json(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        _created_at(),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="nonnegative_latency"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_results_run_id", "evaluation_results", ["run_id"])
    op.create_index("ix_evaluation_results_scenario_id", "evaluation_results", ["scenario_id"])


def downgrade() -> None:
    op.drop_table("evaluation_results")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("approval_requests")
    op.drop_table("tool_executions")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_table("conversation_messages")
    op.drop_table("tickets")
    op.drop_table("repository_permissions")
    op.drop_table("repositories")
    op.drop_table("users")
    op.drop_table("employees")
    # The vector extension may also serve other applications in this database.
