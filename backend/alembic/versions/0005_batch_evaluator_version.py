"""Record batch scorer versions without relabeling historical evaluation results."""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "evaluation_batches",
        sa.Column(
            "evaluator_version",
            sa.String(32),
            nullable=False,
            server_default="deterministic-v2",
        ),
    )
    # Every batch present before this transactional migration used the original scorer.
    # Subsequent inserts receive v2; no historical result payload is rewritten.
    batches = sa.table("evaluation_batches", sa.column("evaluator_version", sa.String(32)))
    op.execute(batches.update().values(evaluator_version="deterministic-v1"))


def downgrade() -> None:
    with op.batch_alter_table("evaluation_batches") as batch:
        batch.drop_column("evaluator_version")
