"""Add run queue and execution lease fields.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column("agent_runs", sa.Column("lease_owner", sa.String(80), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "agent_runs",
        sa.Column("provider_name", sa.String(40), nullable=False, server_default="demo"),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "model_name", sa.String(120), nullable=False, server_default="deterministic-access-v1"
        ),
    )
    op.create_index("ix_agent_runs_lease_expires_at", "agent_runs", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_lease_expires_at", table_name="agent_runs")
    for name in (
        "model_name",
        "provider_name",
        "attempt_count",
        "lease_expires_at",
        "lease_owner",
        "queued_at",
    ):
        op.drop_column("agent_runs", name)
