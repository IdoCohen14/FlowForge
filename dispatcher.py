"""Publish one bounded outbox batch; delivery may be duplicated after a crash."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from models import OutboxEvent
from tasks import process_job

logger = logging.getLogger(__name__)


def dispatch_batch(
    engine: Engine,
    batch_size: int = 20,
    max_attempts: int = 5,
    base_delay_seconds: int = 5,
    max_delay_seconds: int = 300,
    now: datetime | None = None,
) -> int:
    """Return the number claimed. retry_count counts failed publication attempts."""
    if min(batch_size, max_attempts, base_delay_seconds, max_delay_seconds) < 1:
        raise ValueError("Batch size, attempts, and delays must be positive")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    with Session(engine) as session, session.begin():
        events = list(session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.publication_status == "pending",
                OutboxEvent.event_type == "process_job",
                or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= current_time),
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ))
        for event in events:
            if event.retry_count >= max_attempts:
                event.publication_status = "failed"
                event.next_attempt_at = None
                continue
            try:
                # Dispatcher owns retries; avoid Celery's nested publishing retries.
                process_job.apply_async(args=[str(event.job_id)], retry=False)
            except Exception:
                event.retry_count += 1
                if event.retry_count >= max_attempts:
                    event.publication_status = "failed"
                    event.next_attempt_at = None
                else:
                    delay = min(base_delay_seconds, max_delay_seconds)
                    for _ in range(event.retry_count - 1):
                        delay = min(delay * 2, max_delay_seconds)
                        if delay == max_delay_seconds:
                            break
                    event.next_attempt_at = current_time + timedelta(seconds=delay)
                logger.warning("Outbox event %s publication failed (attempt %s, status %s)",
                               event.id, event.retry_count, event.publication_status)
            else:
                event.publication_status = "published"
                event.next_attempt_at = None
        return len(events)
