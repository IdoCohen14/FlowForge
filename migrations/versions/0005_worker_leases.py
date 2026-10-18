"""Bound interrupted processing with fenced worker leases."""
from alembic import op
import sqlalchemy as sa

revision = "0005_worker_leases"
down_revision = "0004_csv_schema"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("claim_token", sa.Uuid(), nullable=True))
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"])
    # Existing running jobs have no lease. Give them a bounded migration grace period.
    op.execute("UPDATE jobs SET lease_expires_at = CURRENT_TIMESTAMP + INTERVAL '5 minutes' WHERE status = 'running'")

def downgrade():
    op.drop_index("ix_jobs_lease_expires_at", table_name="jobs")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "claim_token")
    op.drop_column("jobs", "attempt_count")
