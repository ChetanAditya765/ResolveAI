"""One approval per run and recovery attempts per execution segment.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("recovery_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("approval_requests") as batch:
        batch.create_unique_constraint("uq_approval_request_run", ["run_id"])


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("uq_approval_request_run", type_="unique")
    op.drop_column("agent_runs", "recovery_attempt_count")
