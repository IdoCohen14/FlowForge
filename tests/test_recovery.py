from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
import tasks
from models import Job, OutboxEvent, CsvAnalysis
from recovery import recover_expired_jobs

def seed(engine, attempts=1, expired=True):
    with Session(engine) as session, session.begin():
        job = Job(name="Interrupted", status="running", attempt_count=attempts,
                  claim_token=uuid4(), lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=-1 if expired else 10))
        session.add(job)
        session.flush()
        return job.id

def test_expired_claim_requeues_once(test_engine):
    job_id = seed(test_engine)
    assert recover_expired_jobs(test_engine) == 1
    assert recover_expired_jobs(test_engine) == 0
    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert (job.status, job.claim_token, job.lease_expires_at, job.attempt_count) == ("pending", None, None, 1)
        assert list(session.scalars(select(OutboxEvent.job_id))) == [job_id]
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
        tasks.process_job.run(str(job_id))
    finally:
        monkeypatch.undo()
    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job.status == "completed" and job.attempt_count == 2

def test_active_claim_not_recovered(test_engine):
    seed(test_engine, expired=False)
    assert recover_expired_jobs(test_engine) == 0

def test_recovery_exhaustion_is_terminal(test_engine):
    job_id = seed(test_engine, attempts=3)
    with Session(test_engine) as session, session.begin():
        job = session.get(Job, job_id)
        job.job_type = "csv"
        job.csv_analysis = CsvAnalysis(storage_filename="unused.csv")
    assert recover_expired_jobs(test_engine) == 1
    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job.status == "failed"
        assert job.csv_analysis.error_message == "CSV processing failed"
        assert list(session.scalars(select(OutboxEvent))) == []

def test_stale_worker_cannot_finalize_new_claim(test_engine, monkeypatch):
    with Session(test_engine) as session, session.begin():
        job = Job(name="Fence")
        session.add(job)
        session.flush()
        job_id = job.id
    monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
    replacement = uuid4()
    def operation(name):
        # Simulate expiry, recovery, then a newer claim while the old operation continues.
        assert recover_expired_jobs(test_engine, now=datetime.now(timezone.utc)+timedelta(minutes=6)) == 1
        with Session(test_engine) as session, session.begin():
            job = session.get(Job, job_id)
            job.status = "running"
            job.claim_token = replacement
            job.attempt_count += 1
        return 0
    monkeypatch.setattr(tasks, "demonstrate_processing", operation)
    tasks.process_job.run(str(job_id))
    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job.status == "running" and job.claim_token == replacement

def test_final_commit_failure_recoverable(test_engine, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.exc import DBAPIError
    with Session(test_engine) as session, session.begin():
        job = Job(name="Commit failure")
        session.add(job)
        session.flush()
        job_id = job.id
    monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
    def fail_final(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE jobs") and parameters.get("status") == "completed":
            connection.exec_driver_sql("SELECT 1 / 0")
    event.listen(test_engine, "before_cursor_execute", fail_final)
    try:
        with pytest.raises(DBAPIError):
            tasks.process_job.run(str(job_id))
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_final)
    assert recover_expired_jobs(test_engine, now=datetime.now(timezone.utc)+timedelta(minutes=6)) == 1
    tasks.process_job.run(str(job_id))
    with Session(test_engine) as session:
        assert session.get(Job, job_id).status == "completed"


def test_recovery_skips_locked_job(test_engine):
    job_id = seed(test_engine)
    with Session(test_engine) as owner, owner.begin():
        owner.get(Job, job_id, with_for_update=True)
        assert recover_expired_jobs(test_engine) == 0
    assert recover_expired_jobs(test_engine) == 1

def test_recovery_transaction_rolls_back_claim_and_outbox(test_engine):
    from sqlalchemy import event
    from sqlalchemy.exc import DBAPIError
    job_id = seed(test_engine)
    with Session(test_engine) as session:
        original_token = session.get(Job, job_id).claim_token
    def fail_outbox(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO outbox_events"):
            connection.exec_driver_sql("SELECT 1 / 0")
    event.listen(test_engine, "before_cursor_execute", fail_outbox)
    try:
        with pytest.raises(DBAPIError):
            recover_expired_jobs(test_engine)
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_outbox)
    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job.status == "running" and job.claim_token == original_token
        assert list(session.scalars(select(OutboxEvent))) == []
    assert recover_expired_jobs(test_engine) == 1
