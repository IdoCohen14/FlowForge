"""Database models for persisted jobs."""

from __future__ import annotations

from uuid import UUID, uuid4
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (CheckConstraint("job_type IN ('demo', 'csv')", name="ck_jobs_job_type"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )
    job_type: Mapped[str] = mapped_column(
        String, nullable=False, default="demo", server_default="demo"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String)
    retry_of: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claim_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    csv_analysis: Mapped[CsvAnalysis | None] = relationship(back_populates="job")


class CsvAnalysis(Base):
    __tablename__ = "csv_analyses"

    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    storage_filename: Mapped[str] = mapped_column(String, nullable=False)
    results: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    job: Mapped[Job] = relationship(back_populates="csv_analysis")


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String, nullable=False, default="process_job", server_default="process_job"
    )
    publication_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
