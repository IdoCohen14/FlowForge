"""Demonstration tasks and database-backed job processing."""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from celery_app import celery_app
from database import get_application_engine
from job_lifecycle import ALLOWED_TRANSITIONS
from models import Job

logger = logging.getLogger(__name__)


def demonstrate_processing(name: str) -> int:
    """Compute a repeatable character checksum without external side effects."""
    return sum(ord(character) for character in name)


@celery_app.task
def add(x: int, y: int) -> int:
    return x + y


@celery_app.task
def process_job(job_id: str) -> None:
    parsed_id = UUID(job_id)
    engine = get_application_engine()
    with Session(engine) as session, session.begin():
        job = session.scalar(select(Job).where(Job.id == parsed_id).with_for_update())
        if job is None or job.status != "pending":
            return
        job.status = "running"
        name = job.name
    # The committed claim prevents another delivery from executing the operation.
    processing_error: Exception | None = None
    try:
        demonstrate_processing(name)
    except Exception as error:
        processing_error = error
        logger.exception("Processing job %s failed", job_id)

    target = "failed" if processing_error is not None else "completed"
    with Session(engine) as session, session.begin():
        job = session.scalar(select(Job).where(Job.id == parsed_id).with_for_update())
        if job is not None and job.status == "running":
            if target in ALLOWED_TRANSITIONS[job.status]:
                job.status = target
    if processing_error is not None:
        raise processing_error
