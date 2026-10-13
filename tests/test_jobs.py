from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from main import app, jobs


@pytest.fixture
def client() -> Iterator[TestClient]:
    jobs.clear()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        jobs.clear()


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
