from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from database import get_session, session_scope
from main import app
from models import CsvAnalysis, Job, OutboxEvent
from file_storage import MAX_UPLOAD_BYTES


@pytest.fixture
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(test_engine: Engine, upload_dir: Path) -> Iterator[TestClient]:
    def isolated_session() -> Iterator[Session]:
        yield from session_scope(test_engine)

    app.dependency_overrides[get_session] = isolated_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.parametrize("name", [None, "Sales analysis"])
def test_csv_upload_creates_all_records(client: TestClient, test_engine: Engine, upload_dir: Path, name: str | None) -> None:
    response = client.post("/jobs/csv", files={"file": ("../../sales.csv", b"a,b\n1,2\n", "text/csv")}, data={} if name is None else {"name": name})
    assert response.status_code == 200
    data = response.json()
    assert data == {"id": data["id"], "name": name or "CSV analysis", "status": "pending"}
    with Session(test_engine) as session:
        job = session.get(Job, UUID(data["id"]))
        assert job.job_type == "csv"
        analysis = session.get(CsvAnalysis, job.id)
        assert analysis.results is None
        assert analysis.error_message is None
        assert (upload_dir / analysis.storage_filename).read_bytes() == b"a,b\n1,2\n"
        entries = list(session.scalars(select(OutboxEvent)))
        assert len(entries) == 1
        assert entries[0].job_id == job.id
        assert entries[0].publication_status == "pending"
    assert client.get(f"/jobs/{data['id']}").json() == data


@pytest.mark.parametrize(("filename", "contents", "status"), [
    ("file.txt", b"a\n1", 415), ("file.csv", b"", 422),
    ("file.csv", b"a,a\n1,2", 422), ("file.csv", b"a,b\n1", 422),
    ("file.csv", b'a\n"unfinished', 422), ("file.csv", b"a\n\xff", 422),
])
def test_invalid_upload_leaves_no_files_or_records(client: TestClient, test_engine: Engine, upload_dir: Path, filename: str, contents: bytes, status: int) -> None:
    response = client.post("/jobs/csv", files={"file": (filename, contents)})
    assert response.status_code == status
    assert list(upload_dir.iterdir()) == []
    with Session(test_engine) as session:
        for model in (Job, CsvAnalysis, OutboxEvent):
            assert list(session.scalars(select(model))) == []


def test_upload_size_limit(client: TestClient, upload_dir: Path) -> None:
    response = client.post("/jobs/csv", files={"file": ("big.csv", b"x" * (MAX_UPLOAD_BYTES + 1))})
    assert response.status_code == 413
    assert list(upload_dir.iterdir()) == []
    assert client.get("/jobs").json() == []


def test_missing_upload(client: TestClient) -> None:
    assert client.post("/jobs/csv", data={"name": "Missing"}).status_code == 422


def test_transaction_failure_cleans_only_new_file(client: TestClient, test_engine: Engine, upload_dir: Path) -> None:
    existing = client.post("/jobs/csv", files={"file": ("existing.csv", b"a\n1\n")})
    assert existing.status_code == 200
    existing_files = {path.name: path.read_bytes() for path in upload_dir.iterdir()}

    def fail_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO outbox_events"):
            connection.exec_driver_sql("SELECT 1 / 0")

    event.listen(test_engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(DBAPIError):
            client.post("/jobs/csv", files={"file": ("new.csv", b"a\n2\n")})
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_insert)
    assert {path.name: path.read_bytes() for path in upload_dir.iterdir()} == existing_files
    with Session(test_engine) as session:
        for model in (Job, CsvAnalysis, OutboxEvent):
            assert len(list(session.scalars(select(model)))) == 1


def test_upload_real_sales_sample(
    client: TestClient, test_engine: Engine, upload_dir: Path
) -> None:
    sample = Path(__file__).resolve().parents[1] / "sample_data" / "sales.csv"
    with sample.open("rb") as source:
        response = client.post(
            "/jobs/csv",
            files={"file": (sample.name, source, "text/csv")},
            data={"name": "Sales sample analysis"},
        )

    assert response.status_code == 200
    result = response.json()
    job_id = UUID(result["id"])
    assert result == {
        "id": str(job_id),
        "name": "Sales sample analysis",
        "status": "pending",
    }
    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.job_type == "csv"
        assert job.name == result["name"]
        assert job.status == "pending"
        analysis = session.get(CsvAnalysis, job_id)
        assert analysis is not None
        assert analysis.results is None
        assert analysis.error_message is None
        saved = upload_dir / analysis.storage_filename
        assert saved.is_file()
        assert saved.read_bytes() == sample.read_bytes()
        assert saved.resolve().parent == upload_dir.resolve()
        entries = list(session.scalars(select(OutboxEvent)))
        assert len(entries) == 1
        assert entries[0].job_id == job_id
        assert entries[0].event_type == "process_job"
        assert entries[0].publication_status == "pending"
        assert entries[0].retry_count == 0


@pytest.mark.parametrize("status", ["pending", "running", "failed"])
def test_analysis_noncompleted_status(client: TestClient, test_engine: Engine, status: str) -> None:
    created = client.post("/jobs/csv", files={"file": ("data.csv", b"a\n1\n")})
    job_id = created.json()["id"]
    with Session(test_engine) as session, session.begin():
        job = session.get(Job, UUID(job_id))
        job.status = status
        job.csv_analysis.error_message = "Password and private C:/path must never leak"
    response = client.get(f"/jobs/{job_id}/analysis")
    assert response.status_code == (200 if status == "failed" else 202)
    assert response.json() == {
        "id": job_id, "status": status, "results": None,
        "error_message": "CSV processing failed" if status == "failed" else None,
    }


@pytest.mark.parametrize("job_id", ["missing-job", "00000000-0000-0000-0000-000000000000"])
def test_analysis_missing_job(client: TestClient, job_id: str) -> None:
    response = client.get(f"/jobs/{job_id}/analysis")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_analysis_rejects_demo_job(client: TestClient) -> None:
    created = client.post("/jobs", json={"name": "Demo"})
    response = client.get(f"/jobs/{created.json()['id']}/analysis")
    assert response.status_code == 409
    assert response.json() == {"detail": "Job is not a CSV job"}


def test_analysis_sales_sample(client: TestClient, test_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    import tasks

    monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
    sample = Path(__file__).resolve().parents[1] / "sample_data/sales.csv"
    with sample.open("rb") as source:
        created = client.post("/jobs/csv", files={"file": (sample.name, source)})
    assert created.status_code == 200
    job_id = created.json()["id"]
    tasks.process_job.apply(args=[job_id], throw=True)
    response = client.get(f"/jobs/{job_id}/analysis")
    assert response.status_code == 200
    result = response.json()
    assert result["id"] == job_id
    assert result["status"] == "completed"
    assert result["error_message"] is None
    assert result["results"] == {
        "row_count": 4, "column_count": 3,
        "headers": ["Product", "Price", "Quantity"], "missing_values_total": 0,
        "columns": [
            {"name": "Product", "missing_values": 0, "numeric": None},
            {"name": "Price", "missing_values": 0, "numeric": {"count": 4, "minimum": "25", "maximum": "1200", "mean": "401.25"}},
            {"name": "Quantity", "missing_values": 0, "numeric": {"count": 4, "minimum": "2", "maximum": "10", "mean": "5"}},
        ],
    }
    assert client.get(f"/jobs/{job_id}").json() == {**created.json(), "status": "completed"}


def test_completed_without_results_returns_conflict(client: TestClient, test_engine: Engine) -> None:
    created = client.post("/jobs/csv", files={"file": ("data.csv", b"a\n1\n")})
    job_id = created.json()["id"]
    with Session(test_engine) as session, session.begin():
        session.get(Job, UUID(job_id)).status = "completed"
    response = client.get(f"/jobs/{job_id}/analysis")
    assert response.status_code == 409
    assert response.json() == {"detail": "Completed CSV analysis is unavailable"}


def test_analysis_openapi_documents_responses() -> None:
    operation = app.openapi()["paths"]["/jobs/{job_id}/analysis"]["get"]
    assert {"200", "202", "404", "409"} <= operation["responses"].keys()


def test_ingress_total_body_limit(client):
    response = client.post("/jobs/csv", content=b"x", headers={"content-length": str(12*1024*1024)})
    assert response.status_code == 413
    assert client.get("/jobs").json() == []

def test_csv_status_owned_by_worker(client):
    job = client.post("/jobs/csv", files={"file": ("a.csv", b"a\n1\n")}).json()
    assert client.patch(f"/jobs/{job['id']}/status", json={"status": "running"}).status_code == 409

def test_dashboard_and_ready(client):
    assert client.get("/dashboard").status_code == 200
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_chunked_ingress_limit(client):
    boundary = "flowforge-test"
    def chunks():
        yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.csv\"\r\nContent-Type: text/csv\r\n\r\n").encode()
        for _ in range(12):
            yield b"x" * (1024*1024)
        yield (f"\r\n--{boundary}--\r\n").encode()
    response = client.post("/jobs/csv", content=chunks(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert response.status_code == 413
    assert client.get("/jobs").json() == []
