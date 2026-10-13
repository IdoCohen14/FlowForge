from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine

import tasks
from models import Job
from tasks import add, process_job


def test_add_task() -> None:
    # apply executes locally without publishing to Redis or starting a worker.
    result = add.apply(args=(2, 3), throw=True)

    assert result.successful()
    assert result.get() == 5


@pytest.fixture
def worker_engine(test_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
    return test_engine


def create_job(engine: Engine, status: str = "pending") -> UUID:
    with Session(engine) as session, session.begin():
        job = Job(name="Demo", status=status)
        session.add(job)
        session.flush()
        return job.id


def job_status(engine: Engine, job_id: UUID) -> str:
    with Session(engine) as session:
        return session.get(Job, job_id).status


def test_process_job_success(worker_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = create_job(worker_engine)

    def operation(name: str) -> int:
        assert job_status(worker_engine, job_id) == "running"
        assert name == "Demo"
        return 389

    mock = Mock(side_effect=operation)
    monkeypatch.setattr(tasks, "demonstrate_processing", mock)
    result = process_job.apply(args=[str(job_id)], throw=True)
    assert result.successful()
    assert job_status(worker_engine, job_id) == "completed"
    process_job.apply(args=[str(job_id)], throw=True)
    mock.assert_called_once_with("Demo")


def test_process_job_failure(worker_engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    job_id = create_job(worker_engine)
    operation = Mock(side_effect=RuntimeError("Demonstration failed"))
    monkeypatch.setattr(tasks, "demonstrate_processing", operation)
    with pytest.raises(RuntimeError, match="Demonstration failed"):
        process_job.apply(args=[str(job_id)], throw=True)
    assert job_status(worker_engine, job_id) == "failed"
    assert "Processing job" in caplog.text
    process_job.apply(args=[str(job_id)], throw=True)
    assert operation.call_count == 1


@pytest.mark.parametrize("status", ["running", "completed", "failed"])
def test_nonpending_delivery_is_skipped(worker_engine: Engine, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    job_id = create_job(worker_engine, status)
    operation = Mock()
    monkeypatch.setattr(tasks, "demonstrate_processing", operation)
    process_job.apply(args=[str(job_id)], throw=True)
    operation.assert_not_called()
    assert job_status(worker_engine, job_id) == status


def test_missing_job_is_skipped(worker_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    operation = Mock()
    monkeypatch.setattr(tasks, "demonstrate_processing", operation)
    process_job.apply(args=[str(uuid4())], throw=True)
    operation.assert_not_called()


@pytest.mark.parametrize("terminal", ["completed", "failed"])
@pytest.mark.parametrize("processing_fails", [False, True])
def test_terminal_change_is_preserved(worker_engine: Engine, monkeypatch: pytest.MonkeyPatch, terminal: str, processing_fails: bool) -> None:
    job_id = create_job(worker_engine)

    def operation(name: str) -> int:
        with Session(worker_engine) as session, session.begin():
            job = session.get(Job, job_id, with_for_update=True)
            assert job.status == "running"
            job.status = terminal
        if processing_fails:
            raise RuntimeError("Processing failed")
        return 0

    monkeypatch.setattr(tasks, "demonstrate_processing", operation)
    if processing_fails:
        with pytest.raises(RuntimeError):
            process_job.apply(args=[str(job_id)], throw=True)
    else:
        process_job.apply(args=[str(job_id)], throw=True)
    assert job_status(worker_engine, job_id) == terminal


def test_concurrent_deliveries_execute_once(worker_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = create_job(worker_engine)
    entered = Event()
    release = Event()

    def operation(name: str) -> int:
        entered.set()
        assert release.wait(10)
        return 0

    mock = Mock(side_effect=operation)
    monkeypatch.setattr(tasks, "demonstrate_processing", mock)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(process_job.run, str(job_id))
        try:
            assert entered.wait(10)
            process_job.run(str(job_id))
            assert mock.call_count == 1
        finally:
            release.set()
        first.result(timeout=10)
    assert job_status(worker_engine, job_id) == "completed"


def test_demonstration_is_deterministic() -> None:
    assert tasks.demonstrate_processing("Demo") == 389
    assert tasks.demonstrate_processing("") == 0
