"""Record actual attempt timing and preserve retry lineage."""
from alembic import op
import sqlalchemy as sa
revision = "0006_job_details"
down_revision = "0005_worker_leases"
branch_labels = None
depends_on = None

def upgrade() -> None:
    for name in ("started_at", "finished_at"):
        op.add_column("jobs", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("failure_code", sa.String(), nullable=True))
    op.add_column("jobs", sa.Column("retry_of", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_jobs_retry_of", "jobs", "jobs", ["retry_of"], ["id"])
    op.create_unique_constraint("uq_jobs_retry_of", "jobs", ["retry_of"])

def downgrade() -> None:
    op.drop_constraint("uq_jobs_retry_of", "jobs", type_="unique")
    op.drop_constraint("fk_jobs_retry_of", "jobs", type_="foreignkey")
    for name in ("retry_of", "failure_code", "finished_at", "started_at"):
        op.drop_column("jobs", name)
