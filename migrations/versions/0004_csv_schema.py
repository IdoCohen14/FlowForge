"""Add job types and one-to-one CSV analysis storage."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_csv_schema"
down_revision = "0003_outbox_retry_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("job_type", sa.String(), server_default="demo", nullable=False))
    op.create_check_constraint("ck_jobs_job_type", "jobs", "job_type IN ('demo', 'csv')")
    op.create_table(
        "csv_analyses",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("storage_filename", sa.String(), nullable=False),
        sa.Column("results", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
    )


def downgrade() -> None:
    op.drop_table("csv_analyses")
    op.drop_constraint("ck_jobs_job_type", "jobs", type_="check")
    op.drop_column("jobs", "job_type")
