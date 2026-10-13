from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from main import app
from database import get_session, session_scope
from sqlalchemy.engine import Engine
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from models import Job, OutboxEvent


@pytest.fixture
def client(test_engine: Engine) -> Iterator[TestClient]:
    def isolated_session() -> Iterator[Session]:
        yield from session_scope(test_engine)

    app.dependency_overrides[get_session] = isolated_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_create_job(client: TestClient) -> None:
    response = client.post("/jobs", json={"name": "Process report"})

    assert response.status_code == 200
    job = response.json()
    assert UUID(job["id"]).version == 4
    assert job == {
        "id": job["id"],
        "name": "Process report",
        "status": "pending",
    }


def test_job_ids_are_unique(client: TestClient) -> None:
    first = client.post("/jobs", json={"name": "Same name"})
    second = client.post("/jobs", json={"name": "Same name"})

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] != second.json()["id"]
    response = client.get("/jobs")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_retrieve_job(client: TestClient) -> None:
    created = client.post("/jobs", json={"name": "Send notification"})
    assert created.status_code == 200
    job = created.json()

    response = client.get(f"/jobs/{job['id']}")

    assert response.status_code == 200
    assert response.json() == job


def test_list_jobs(client: TestClient) -> None:
    expected = []
    for name in ("First job", "Second job"):
        response = client.post("/jobs", json={"name": name})
        assert response.status_code == 200
        expected.append(response.json())

    response = client.get("/jobs")

    assert response.status_code == 200
    assert {job["id"]: job for job in response.json()} == {
        job["id"]: job for job in expected
    }
    assert len(response.json()) == len(expected)


def test_list_jobs_when_empty(client: TestClient) -> None:
    response = client.get("/jobs")

    assert response.status_code == 200
    assert response.json() == []


def test_retrieve_missing_job(client: TestClient) -> None:
    response = client.get("/jobs/missing-job")

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}


def test_create_job_without_body(client: TestClient) -> None:
    response = client.post("/jobs")

    assert response.status_code == 422
    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": None}, {"name": 123}],
    ids=["missing-name", "null-name", "integer-name"],
)
def test_create_job_with_invalid_name(
    client: TestClient, payload: dict[str, object]
) -> None:
    response = client.post("/jobs", json=payload)

    assert response.status_code == 422
    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert listed.json() == []


def test_home(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to FlowForge!"}


def test_retrieve_two_different_jobs(client: TestClient) -> None:
    first = client.post("/jobs", json={"name": "Process report"})
    second = client.post("/jobs", json={"name": "Send notification"})
    assert first.status_code == second.status_code == 200
    first_job = first.json()
    second_job = second.json()
    assert first_job["id"] != second_job["id"]

    for expected in (first_job, second_job):
        response = client.get(f"/jobs/{expected['id']}")

        assert response.status_code == 200
        assert response.json() == expected


@pytest.mark.parametrize(
    "statuses",
    [("running",), ("running", "completed"), ("running", "failed")],
    ids=["start", "complete", "fail"],
)
def test_successful_status_transitions(
    client: TestClient, statuses: tuple[str, ...]
) -> None:
    created = client.post("/jobs", json={"name": "Lifecycle job"})
    assert created.status_code == 200
    job = created.json()
    for status in statuses:
        response = client.patch(f"/jobs/{job['id']}/status", json={"status": status})
        assert response.status_code == 200
        assert response.json() == {**job, "status": status}

    retrieved = client.get(f"/jobs/{job['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == {**job, "status": statuses[-1]}
    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert listed.json() == [retrieved.json()]


@pytest.mark.parametrize(
    ("current_status", "new_status"),
    [
        ("pending", "pending"),
        ("pending", "completed"),
        ("pending", "failed"),
        ("running", "pending"),
        ("running", "running"),
        ("completed", "pending"),
        ("completed", "running"),
        ("completed", "completed"),
        ("completed", "failed"),
        ("failed", "pending"),
        ("failed", "running"),
        ("failed", "completed"),
        ("failed", "failed"),
    ],
)
def test_invalid_status_transitions(
    client: TestClient, current_status: str, new_status: str
) -> None:
    created = client.post("/jobs", json={"name": "Lifecycle job"})
    assert created.status_code == 200
    job = created.json()
    status_url = f"/jobs/{job['id']}/status"
    if current_status != "pending":
        assert client.patch(status_url, json={"status": "running"}).status_code == 200
    if current_status in ("completed", "failed"):
        assert client.patch(status_url, json={"status": current_status}).status_code == 200

    response = client.patch(status_url, json={"status": new_status})

    assert response.status_code == 409
    assert response.json() == {"detail": "Invalid status transition"}
    retrieved = client.get(f"/jobs/{job['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == {**job, "status": current_status}


@pytest.mark.parametrize(
    "payload",
    [{"status": "unknown"}, {"status": "RUNNING"}, {"status": None}, {"status": 123}, {}],
    ids=["unsupported", "wrong-case", "null", "integer", "missing-status"],
)
def test_invalid_status_request(
    client: TestClient, payload: dict[str, object]
) -> None:
    created = client.post("/jobs", json={"name": "Lifecycle job"})
    assert created.status_code == 200
    job = created.json()

    response = client.patch(f"/jobs/{job['id']}/status", json=payload)

    assert response.status_code == 422
    retrieved = client.get(f"/jobs/{job['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == job


def test_status_update_without_body(client: TestClient) -> None:
    created = client.post("/jobs", json={"name": "Lifecycle job"})
    assert created.status_code == 200
    job = created.json()

    response = client.patch(f"/jobs/{job['id']}/status")

    assert response.status_code == 422
    retrieved = client.get(f"/jobs/{job['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == job


def test_status_update_for_missing_job(client: TestClient) -> None:
    response = client.patch("/jobs/missing-job/status", json={"status": "running"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}
    assert client.get("/jobs").json() == []


def test_job_persists_across_sessions(client: TestClient, test_engine: Engine) -> None:
    created = client.post("/jobs", json={"name": "Persistent job"})
    assert created.status_code == 200
    job_id = created.json()["id"]
    assert client.patch(f"/jobs/{job_id}/status", json={"status": "running"}).status_code == 200

    with Session(test_engine) as session:
        stored = session.get(Job, UUID(job_id))
        assert stored is not None
        assert stored.name == "Persistent job"
        assert stored.status == "running"


@pytest.mark.parametrize("operation", ["create", "update"])
def test_database_failure_rolls_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, operation: str, test_engine: Engine
) -> None:
    if operation == "update":
        created = client.post("/jobs", json={"name": "Existing job"})
        assert created.status_code == 200
        job = created.json()

    def failing_commit(session: Session) -> None:
        session.flush()
        session.execute(text("SELECT 1 / 0"))

    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", failing_commit)
        with pytest.raises(DBAPIError):
            if operation == "create":
                client.post("/jobs", json={"name": "Uncommitted job"})
            else:
                client.patch(f"/jobs/{job['id']}/status", json={"status": "running"})

    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert listed.json() == ([] if operation == "create" else [job])
    with Session(test_engine) as session:
        events = list(session.scalars(select(OutboxEvent)))
        assert len(events) == (0 if operation == "create" else 1)
    # A later write also succeeds after the failed transaction.
    assert client.post("/jobs", json={"name": "After failure"}).status_code == 200


def test_create_job_with_outbox_event(client: TestClient, test_engine: Engine) -> None:
    first = client.post("/jobs", json={"name": "First job"})
    second = client.post("/jobs", json={"name": "Second job"})
    assert first.status_code == second.status_code == 200

    with Session(test_engine) as session:
        events = list(session.scalars(select(OutboxEvent)))
        assert len(events) == 2
        assert len({entry.id for entry in events}) == 2
        assert {str(entry.job_id) for entry in events} == {
            first.json()["id"], second.json()["id"]
        }
        for entry in events:
            assert isinstance(entry.id, UUID)
            assert entry.event_type == "process_job"
            assert entry.publication_status == "pending"
            assert entry.retry_count == 0
            assert entry.created_at.tzinfo is not None
            stored = session.get(Job, entry.job_id)
            assert stored is not None
            assert stored.status == "pending"


def test_outbox_insert_failure_rolls_back_job(
    client: TestClient, test_engine: Engine
) -> None:
    def fail_outbox_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO outbox_events"):
            connection.exec_driver_sql("SELECT 1 / 0")

    event.listen(test_engine, "before_cursor_execute", fail_outbox_insert)
    try:
        with pytest.raises(DBAPIError):
            client.post("/jobs", json={"name": "Rolled back job"})
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_outbox_insert)

    with Session(test_engine) as session:
        assert list(session.scalars(select(Job))) == []
        assert list(session.scalars(select(OutboxEvent))) == []
    assert client.post("/jobs", json={"name": "After rollback"}).status_code == 200
