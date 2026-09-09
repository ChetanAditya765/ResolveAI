"""Durable scenario batches and versioned live/scenario evaluation records."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "evaluation_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "requested_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("submission_key", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("active_slot", sa.Integer()),
        sa.Column("scenario_ids", JSON_VALUE, nullable=False),
        sa.Column("catalog_version", sa.String(32), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(80)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("submission_key", name="uq_evaluation_batches_submission_key"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')", name="batch_status"
        ),
        sa.CheckConstraint(
            "completed_count >= 0 AND completed_count <= total_count", name="batch_progress"
        ),
    )
    op.create_index(
        "uq_evaluation_active_batch", "evaluation_batches", ["active_slot"], unique=True
    )
    op.create_index(
        "ix_evaluation_batches_lease_expires_at", "evaluation_batches", ["lease_expires_at"]
    )
    with op.batch_alter_table("evaluation_results") as batch:
        batch.add_column(sa.Column("source", sa.String(16), nullable=False, server_default="live"))
        batch.add_column(sa.Column("batch_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column("snapshot", JSON_VALUE, nullable=False, server_default=sa.text("'{}'"))
        )
        batch.create_foreign_key(
            "fk_evaluation_results_batch_id_evaluation_batches",
            "evaluation_batches",
            ["batch_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_evaluation_results_batch_id", ["batch_id"])
        batch.create_check_constraint("evaluation_source", "source IN ('live', 'scenario')")
        batch.create_unique_constraint("uq_evaluation_run_version", ["run_id", "evaluator_version"])
        batch.create_unique_constraint(
            "uq_evaluation_batch_scenario", ["batch_id", "scenario_id", "evaluator_version"]
        )


def downgrade() -> None:
    with op.batch_alter_table("evaluation_results") as batch:
        batch.drop_constraint("uq_evaluation_batch_scenario", type_="unique")
        batch.drop_constraint("uq_evaluation_run_version", type_="unique")
        batch.drop_constraint(op.f("ck_evaluation_results_evaluation_source"), type_="check")
        batch.drop_index("ix_evaluation_results_batch_id")
        batch.drop_constraint(
            "fk_evaluation_results_batch_id_evaluation_batches", type_="foreignkey"
        )
        batch.drop_column("snapshot")
        batch.drop_column("batch_id")
        batch.drop_column("source")
    op.drop_table("evaluation_batches")
