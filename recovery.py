"""Recover expired claims transactionally; stale workers cannot finalize them."""
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from models import Job, OutboxEvent

MAX_PROCESSING_ATTEMPTS = 3

def recover_expired_jobs(engine: Engine, batch_size: int = 20, now: datetime | None = None) -> int:
    if batch_size < 1:
        raise ValueError("Batch size must be positive")
    if now is not None and now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    with Session(engine) as session, session.begin():
        current = now if now is not None else session.scalar(select(func.now()))
        jobs = list(session.scalars(select(Job).where(
            Job.status == "running", Job.lease_expires_at <= current,
        ).order_by(Job.lease_expires_at, Job.id).limit(batch_size).with_for_update(skip_locked=True)))
        for job in jobs:
            job.claim_token = None
            job.lease_expires_at = None
            if job.attempt_count >= MAX_PROCESSING_ATTEMPTS:
                job.status = "failed"
                if job.csv_analysis is not None:
                    job.csv_analysis.results = None
                    job.csv_analysis.error_message = "CSV processing failed"
            else:
                job.status = "pending"
                session.add(OutboxEvent(job_id=job.id))
        return len(jobs)
