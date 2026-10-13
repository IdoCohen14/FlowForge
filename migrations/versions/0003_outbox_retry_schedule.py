"""Persist the outbox retry schedule."""

from alembic import op
import sqlalchemy as sa

revision = "0003_outbox_retry_schedule"
down_revision = "0002_create_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox_events", "next_attempt_at")
