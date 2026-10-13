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
import dispatcher
from sqlalchemy.exc import OperationalError


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


@pytest.mark.parametrize("claimed", [0, 2])
def test_polling_waits_between_batches(monkeypatch: pytest.MonkeyPatch, claimed: int) -> None:
    batch = Mock(return_value=claimed)
    sleep = Mock(side_effect=[None, KeyboardInterrupt()])
    monkeypatch.setattr(dispatcher, "dispatch_batch", batch)
    monkeypatch.setattr(dispatcher.time, "sleep", sleep)
    engine = Mock()
    dispatcher.run_dispatcher(engine, interval=0.5, batch_size=3)
    assert batch.call_count == sleep.call_count == 2
    sleep.assert_called_with(0.5)
    batch.assert_called_with(engine, batch_size=3, max_attempts=5,
                             base_delay_seconds=5, max_delay_seconds=300)


@pytest.mark.parametrize("error", [OperationalError(None, None, Exception()), ConnectionError(), TimeoutError()])
def test_polling_recovers_after_temporary_failure(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    batch = Mock(side_effect=[error, 0])
    sleep = Mock(side_effect=[None, KeyboardInterrupt()])
    monkeypatch.setattr(dispatcher, "dispatch_batch", batch)
    monkeypatch.setattr(dispatcher.time, "sleep", sleep)
    dispatcher.run_dispatcher(Mock())
    assert batch.call_count == sleep.call_count == 2
    sleep.assert_called_with(1.0)


def test_shutdown_during_batch_disposes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Mock()
    monkeypatch.setattr(dispatcher, "get_engine", Mock(return_value=engine))
    monkeypatch.setattr(dispatcher, "dispatch_batch", Mock(side_effect=KeyboardInterrupt()))
    sleep = Mock()
    monkeypatch.setattr(dispatcher.time, "sleep", sleep)
    dispatcher.main([])
    sleep.assert_not_called()
    engine.dispose.assert_called_once()


def test_unexpected_failure_propagates_and_disposes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = Mock()
    monkeypatch.setattr(dispatcher, "get_engine", Mock(return_value=engine))
    monkeypatch.setattr(dispatcher, "dispatch_batch", Mock(side_effect=RuntimeError("Bug")))
    with pytest.raises(RuntimeError, match="Bug"):
        dispatcher.main([])
    engine.dispose.assert_called_once()


@pytest.mark.parametrize("interval", ["0", "-1", "nan", "inf"])
def test_invalid_interval_stops_before_database_access(monkeypatch: pytest.MonkeyPatch, interval: str) -> None:
    engine_factory = Mock()
    monkeypatch.setattr(dispatcher, "get_engine", engine_factory)
    with pytest.raises(SystemExit):
        dispatcher.main(["--interval", interval])
    engine_factory.assert_not_called()


def test_interrupted_batch_rolls_back_and_releases_lock(test_engine: Engine, publisher: Mock) -> None:
    event_id = seed_event(test_engine)
    publisher.side_effect = KeyboardInterrupt()
    dispatcher.run_dispatcher(test_engine)
    with Session(test_engine) as session:
        entry = session.get(OutboxEvent, event_id)
        assert entry.publication_status == "pending"
        assert entry.retry_count == 0
    publisher.side_effect = None
    assert dispatch_batch(test_engine) == 1
