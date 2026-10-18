"""Publish one bounded outbox batch; delivery may be duplicated after a crash."""

import logging
import argparse
import math
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from models import OutboxEvent
from recovery import recover_expired_jobs
from tasks import process_job
from database import get_engine

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


def run_dispatcher(
    engine: Engine,
    interval: float = 1.0,
    batch_size: int = 20,
    max_attempts: int = 5,
    base_delay_seconds: int = 5,
    max_delay_seconds: int = 300,
) -> None:
    """Poll until Ctrl+C. The caller owns and disposes the engine."""
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Polling interval must be finite and positive")
    if min(batch_size, max_attempts, base_delay_seconds, max_delay_seconds) < 1:
        raise ValueError("Batch size, attempts, and delays must be positive")
    try:
        while True:
            try:
                recover_expired_jobs(engine, batch_size=batch_size)
                claimed = dispatch_batch(
                    engine, batch_size=batch_size, max_attempts=max_attempts,
                    base_delay_seconds=base_delay_seconds,
                    max_delay_seconds=max_delay_seconds,
                )
                if claimed:
                    logger.info("Processed outbox batch: %s events claimed", claimed)
            except (OperationalError, ConnectionError, TimeoutError):
                # Batch context managers already roll back and close the session.
                logger.warning("Temporary dispatcher failure; retrying after polling interval")
            # Always wait, including after empty batches or transient failures.
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Dispatcher stopped")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Continuously publish FlowForge outbox events")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--base-delay-seconds", type=int, default=5)
    parser.add_argument("--max-delay-seconds", type=int, default=300)
    options = parser.parse_args(argv)
    if not math.isfinite(options.interval) or options.interval <= 0:
        parser.error("--interval must be finite and positive")
    if min(options.batch_size, options.max_attempts, options.base_delay_seconds, options.max_delay_seconds) < 1:
        parser.error("batch size, attempts, and retry delays must be positive")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = get_engine()
    try:
        run_dispatcher(engine, interval=options.interval, batch_size=options.batch_size,
                       max_attempts=options.max_attempts,
                       base_delay_seconds=options.base_delay_seconds,
                       max_delay_seconds=options.max_delay_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
