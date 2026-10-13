from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import Mock
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from dispatcher import dispatch_batch
from models import Job, OutboxEvent
from tasks import process_job


@pytest.fixture
def publisher(monkeypatch: pytest.MonkeyPatch) -> Mock:
    mock = Mock()
    monkeypatch.setattr(process_job, "apply_async", mock)
    return mock


def seed_event(engine: Engine) -> UUID:
    with Session(engine) as session, session.begin():
        job = Job(name="Dispatch job")
        session.add(job)
        session.flush()
        entry = OutboxEvent(job_id=job.id)
        session.add(entry)
        session.flush()
        return entry.id


def test_publish_bounded_batch(test_engine: Engine, publisher: Mock) -> None:
    seed_event(test_engine)
    seed_event(test_engine)
    assert dispatch_batch(test_engine, batch_size=1) == 1
    assert publisher.call_count == 1
    with Session(test_engine) as session:
        entries = list(session.scalars(select(OutboxEvent)))
        published = next(entry for entry in entries if entry.publication_status == "published")
        assert sum(entry.publication_status == "pending" for entry in entries) == 1
        publisher.assert_called_once_with(args=[str(published.job_id)], retry=False)
    assert dispatch_batch(test_engine) == 1
    assert dispatch_batch(test_engine) == 0


def test_retry_backoff_and_exhaustion(test_engine: Engine, publisher: Mock) -> None:
    event_id = seed_event(test_engine)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    publisher.side_effect = ConnectionError("Broker unavailable")
    for attempt, elapsed, expected_delay in [(1, 0, 5), (2, 5, 8), (3, 13, None)]:
        current = now + timedelta(seconds=elapsed)
        assert dispatch_batch(test_engine, max_attempts=3, max_delay_seconds=8, now=current) == 1
        with Session(test_engine) as session:
            entry = session.get(OutboxEvent, event_id)
            assert entry.retry_count == attempt
            if expected_delay is None:
                assert entry.publication_status == "failed"
                assert entry.next_attempt_at is None
            else:
                assert entry.publication_status == "pending"
                assert entry.next_attempt_at == current + timedelta(seconds=expected_delay)
                assert dispatch_batch(test_engine, now=current) == 0
    assert dispatch_batch(test_engine, now=now + timedelta(days=1)) == 0
    assert publisher.call_count == 3


def test_successful_retry(test_engine: Engine, publisher: Mock) -> None:
    event_id = seed_event(test_engine)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    publisher.side_effect = [ConnectionError(), None]
    assert dispatch_batch(test_engine, now=now) == 1
    assert dispatch_batch(test_engine, now=now + timedelta(seconds=5)) == 1
    with Session(test_engine) as session:
        entry = session.get(OutboxEvent, event_id)
        assert entry.publication_status == "published"
        assert entry.retry_count == 1


def test_concurrent_dispatchers_skip_locked(test_engine: Engine, publisher: Mock) -> None:
    seed_event(test_engine)
    seed_event(test_engine)
    entered = Event()
    release = Event()

    def publish(**kwargs: object) -> None:
        if publisher.call_count == 1:
            entered.set()
            assert release.wait(10)

    publisher.side_effect = publish
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(dispatch_batch, test_engine, batch_size=1)
        try:
            assert entered.wait(10)
            assert dispatch_batch(test_engine, batch_size=1) == 1
            assert dispatch_batch(test_engine) == 0
        finally:
            release.set()
        assert first.result(timeout=10) == 1
    arguments = [call.kwargs["args"][0] for call in publisher.call_args_list]
    assert len(arguments) == len(set(arguments)) == 2


def test_database_failure_rolls_back_publication_marker(test_engine: Engine, publisher: Mock) -> None:
    event_id = seed_event(test_engine)

    def fail_update(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE outbox_events"):
            connection.exec_driver_sql("SELECT 1 / 0")

    event.listen(test_engine, "before_cursor_execute", fail_update)
    try:
        with pytest.raises(DBAPIError):
            dispatch_batch(test_engine)
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_update)
    with Session(test_engine) as session:
        assert session.get(OutboxEvent, event_id).publication_status == "pending"
    assert dispatch_batch(test_engine) == 1
    assert publisher.call_count == 2  # Publication can repeat after a DB failure.
