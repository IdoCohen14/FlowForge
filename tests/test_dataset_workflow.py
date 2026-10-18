from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import tasks
from csv_analysis import CsvValidationError, analyze_csv
from database import get_session, session_scope
from main import app, retry_job
from models import Job, CsvAnalysis, OutboxEvent


@pytest.fixture
def client(test_engine, tmp_path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
    def isolated_session():
        yield from session_scope(test_engine)
    app.dependency_overrides[get_session] = isolated_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


def upload(client):
    response = client.post("/jobs/csv", files={"file": ("sales.csv", b"Product,Price\nApple,2\nOrange,4\n")})
    assert response.status_code == 200
    return response.json()["id"]


def fail_processing(client, monkeypatch):
    job_id = upload(client)
    def failed(path):
        raise RuntimeError("private password /uploads/secret.csv")
    monkeypatch.setattr(tasks, "analyze_csv", failed)
    with pytest.raises(RuntimeError):
        tasks.process_job.run(job_id)
    monkeypatch.setattr(tasks, "analyze_csv", analyze_csv)
    return job_id


def test_preview_is_bounded_and_leaves_no_job_or_file(client, test_engine, tmp_path):
    content = "a,b,c,d,e,f,g,h,i\n" + ("1,2,3,4,5,6,7,8,9\n"*7)
    response = client.post("/jobs/csv/preview", files={"file": ("preview.csv", content.encode())})
    assert response.status_code == 200
    preview = response.json()
    assert len(preview["headers"]) == 8 and preview["column_count"] == 9
    assert len(preview["rows"]) == 5 and preview["has_more_rows"] is True
    assert all(len(row) == 8 for row in preview["rows"])
    assert client.get("/jobs").json() == []
    assert list(tmp_path.iterdir()) == []
    with Session(test_engine) as session:
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


@pytest.mark.parametrize("content", [b"", b"a,a\n1,2", b"a,b\n1", b"a\n\xff"])
def test_invalid_preview_cleans_up(client, tmp_path, content):
    assert client.post("/jobs/csv/preview", files={"file": ("bad.csv", content)}).status_code == 422
    assert list(tmp_path.iterdir()) == []
    assert client.get("/jobs").json() == []


def test_preview_limits_and_header_only(client, tmp_path):
    long_cell = "x"*220
    response = client.post("/jobs/csv/preview", files={"file": ("long.csv", ("a\n"+long_cell).encode())}).json()
    assert response["truncated_cells"] is True and len(response["rows"][0][0]) == 200
    header = client.post("/jobs/csv/preview", files={"file": ("header.csv", b"a,b\n")}).json()
    assert header["rows"] == [] and header["has_more_rows"] is False
    oversized = client.post("/jobs/csv/preview", content=b"x", headers={"content-length": str(12*1024*1024)})
    assert oversized.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_worker_records_actual_timing_and_details_are_sanitized(client):
    job_id = upload(client)
    pending = client.get(f"/jobs/{job_id}/details").json()
    assert pending["started_at"] is None and pending["processing_seconds"] is None
    tasks.process_job.run(job_id)
    response = client.get(f"/jobs/{job_id}/details")
    details = response.json()
    assert details["started_at"] <= details["finished_at"]
    assert details["processing_seconds"] >= 0
    assert details["created_at"] is not None
    assert details["recovery_action"] is None
    assert "storage_filename" not in response.text and "claim_token" not in response.text


def test_processing_retry_is_linked_and_idempotent(client, test_engine, monkeypatch):
    original = fail_processing(client, monkeypatch)
    details = client.get(f"/jobs/{original}/details").json()
    assert details["failure_stage"] == "processing" and details["recovery_action"] == "retry_processing"
    assert "private password" not in str(details)
    response = client.post(f"/jobs/{original}/retry")
    assert response.status_code == 200
    retry_id = response.json()["id"]
    assert retry_id != original
    assert client.post(f"/jobs/{original}/retry").json()["id"] == retry_id
    assert client.get(f"/jobs/{original}").json()["status"] == "failed"
    assert client.get(f"/jobs/{retry_id}/details").json()["retry_of"] == original
    tasks.process_job.run(retry_id)
    assert client.get(f"/jobs/{retry_id}/analysis").json()["results"]["row_count"] == 2
    assert client.post(f"/jobs/{original}/retry").json()["id"] == retry_id
    with Session(test_engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 2


def test_concurrent_retry_creates_one_child(client, test_engine, monkeypatch):
    original = fail_processing(client, monkeypatch)
    def retry():
        with Session(test_engine, expire_on_commit=False) as session:
            return retry_job(original, session)["id"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: retry(), range(2)))
    assert ids[0] == ids[1]
    with Session(test_engine) as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.retry_of == UUID(original))) == 1


def test_retry_rolls_back_new_job_and_event(client, test_engine, monkeypatch):
    original = fail_processing(client, monkeypatch)
    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO outbox_events"):
            connection.exec_driver_sql("SELECT 1 / 0")
    event.listen(test_engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(DBAPIError):
            client.post(f"/jobs/{original}/retry")
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_insert)
    with Session(test_engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.get(Job, UUID(original)).status == "failed"
    assert client.post(f"/jobs/{original}/retry").status_code == 200


def test_delivery_recovery_requeues_original_event(client, test_engine):
    job_id = upload(client)
    with Session(test_engine) as session, session.begin():
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == UUID(job_id)))
        outbox.publication_status = "failed"
        outbox.retry_count = 5
    details = client.get(f"/jobs/{job_id}/details").json()
    assert details["failure_stage"] == "delivery" and details["recovery_action"] == "retry_delivery"
    assert client.post(f"/jobs/{job_id}/retry").json()["id"] == job_id
    with Session(test_engine) as session:
        outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == UUID(job_id)))
        assert outbox.publication_status == "pending" and outbox.retry_count == 0
        assert session.scalar(select(func.count()).select_from(Job)) == 1


@pytest.mark.parametrize("reason", ["validation", "storage"])
def test_file_failures_require_new_upload(client, test_engine, tmp_path, reason):
    job_id = upload(client)
    with Session(test_engine) as session:
        path = tmp_path / session.get(CsvAnalysis, UUID(job_id)).storage_filename
    if reason == "validation":
        path.write_bytes(b"a,b\n1\n")
    else:
        path.unlink()
    with pytest.raises((CsvValidationError, OSError)):
        tasks.process_job.run(job_id)
    details = client.get(f"/jobs/{job_id}/details").json()
    assert details["failure_stage"] == reason and details["recovery_action"] == "upload"
    assert client.post(f"/jobs/{job_id}/retry").status_code == 409


def test_retry_rejects_active_completed_and_demo_jobs(client):
    job_id = upload(client)
    assert client.post(f"/jobs/{job_id}/retry").status_code == 409
    tasks.process_job.run(job_id)
    assert client.post(f"/jobs/{job_id}/retry").status_code == 409
    assert client.get("/jobs/not-a-uuid/details").status_code == 404
    assert client.post("/jobs/not-a-uuid/retry").status_code == 404


def test_delivery_retry_preserves_historical_failed_events(client, test_engine):
    from datetime import datetime, timedelta, timezone
    job_id = upload(client)
    with Session(test_engine) as session, session.begin():
        latest = session.scalar(select(OutboxEvent).where(OutboxEvent.job_id == UUID(job_id)))
        latest.publication_status = "failed"
        latest.retry_count = 5
        session.add(OutboxEvent(job_id=UUID(job_id), publication_status="failed", retry_count=7,
            created_at=datetime.now(timezone.utc)-timedelta(days=1)))
    assert client.post(f"/jobs/{job_id}/retry").status_code == 200
    with Session(test_engine) as session:
        historical = session.scalar(select(OutboxEvent).where(OutboxEvent.retry_count == 7))
        assert historical.publication_status == "failed"
        assert session.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.publication_status == "pending")) == 1


def test_interrupted_job_diagnostics_and_historical_timing(client, test_engine):
    job_id = upload(client)
    with Session(test_engine) as session, session.begin():
        job = session.get(Job, UUID(job_id))
        job.status = "failed"
        job.failure_code = "interrupted"
    details = client.get(f"/jobs/{job_id}/details").json()
    assert details["failure_stage"] == "interrupted"
    assert details["recovery_action"] == "retry_processing"
    assert details["processing_seconds"] is None and details["started_at"] is None
    demo = client.post("/jobs", json={"name":"Legacy demo"}).json()["id"]
    with Session(test_engine) as session, session.begin():
        session.get(Job, UUID(demo)).status = "failed"
    assert client.post(f"/jobs/{demo}/retry").status_code == 409


def test_preview_does_not_delete_other_uploads(client, test_engine, tmp_path):
    upload(client)
    before = {p.name:p.read_bytes() for p in tmp_path.iterdir()}
    assert client.post("/jobs/csv/preview", files={"file":("preview.csv",b"a\n1\n")}).status_code == 200
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()} == before
    assert len(client.get("/jobs").json()) == 1


def test_crash_detection_does_not_invent_finish_time(client, test_engine):
    from datetime import datetime, timedelta, timezone
    from recovery import recover_expired_jobs
    job_id = upload(client)
    with Session(test_engine) as session, session.begin():
        job = session.get(Job, UUID(job_id))
        job.status = "running"
        job.attempt_count = 3
        job.started_at = datetime.now(timezone.utc)-timedelta(minutes=10)
        job.lease_expires_at = datetime.now(timezone.utc)-timedelta(minutes=5)
    assert recover_expired_jobs(test_engine) == 1
    details = client.get(f"/jobs/{job_id}/details").json()
    assert details["failure_stage"] == "interrupted"
    assert details["started_at"] is not None
    assert details["finished_at"] is None and details["processing_seconds"] is None
