"""Demonstration tasks and database-backed job processing."""

import logging
from uuid import UUID, uuid4
from datetime import timedelta

from sqlalchemy import func

from sqlalchemy import select
from sqlalchemy.orm import Session

from celery_app import celery_app
from database import get_application_engine
from job_lifecycle import ALLOWED_TRANSITIONS
from models import CsvAnalysis, Job
from csv_analysis import CsvValidationError, analyze_csv
from file_storage import resolve_storage_path

logger = logging.getLogger(__name__)


def demonstrate_processing(name: str) -> int:
    """Compute a repeatable character checksum without external side effects."""
    return sum(ord(character) for character in name)


@celery_app.task
def add(x: int, y: int) -> int:
    return x + y


@celery_app.task(acks_late=True, reject_on_worker_lost=True)
def process_job(job_id: str) -> None:
    parsed_id = UUID(job_id)
    engine = get_application_engine()
    with Session(engine) as session, session.begin():
        job = session.scalar(select(Job).where(Job.id == parsed_id).with_for_update())
        if job is None or job.status != "pending":
            return
        job.status = "running"
        token = uuid4()
        job.claim_token = token
        job.attempt_count += 1
        job.lease_expires_at = session.scalar(select(func.now())) + timedelta(minutes=5)
        name = job.name
        job_type = job.job_type
    # The committed claim prevents another delivery from executing the operation.
    processing_error: Exception | None = None
    results: dict | None = None
    safe_error: str | None = None
    try:
        if job_type == "demo":
            demonstrate_processing(name)
        elif job_type == "csv":
            with Session(engine) as session:
                analysis = session.get(CsvAnalysis, parsed_id)
                if analysis is None:
                    raise ValueError("CSV metadata is missing")
                filename = analysis.storage_filename
            results = analyze_csv(resolve_storage_path(filename))
        else:
            raise ValueError("Unsupported job type")
    except Exception as error:
        processing_error = error
        safe_error = str(error) if isinstance(error, CsvValidationError) else "CSV processing failed"
        logger.exception("Processing job %s failed", job_id)

    target = "failed" if processing_error is not None else "completed"
    with Session(engine) as session, session.begin():
        job = session.scalar(select(Job).where(Job.id == parsed_id).with_for_update())
        if job is not None and job.status == "running" and job.claim_token == token:
            if target in ALLOWED_TRANSITIONS[job.status]:
                if job_type == "csv":
                    analysis = session.get(CsvAnalysis, parsed_id)
                    if analysis is not None:
                        analysis.results = results if processing_error is None else None
                        analysis.error_message = safe_error
                job.status = target
                job.claim_token = None
                job.lease_expires_at = None
    if processing_error is not None:
        raise processing_error
