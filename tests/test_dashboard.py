from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database import get_session, session_scope
from main import app
from models import Job, OutboxEvent


@pytest.fixture
def client(test_engine, tmp_path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    def isolated_session():
        yield from session_scope(test_engine)
    app.dependency_overrides[get_session] = isolated_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


def seed_jobs(engine):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = []
    with Session(engine) as session, session.begin():
        for index, (name, status) in enumerate([
            ("Sales North", "completed"), ("Sales South", "running"),
            ("Inventory", "pending"), ("100% real_sales", "failed"),
        ]):
            job = Job(name=name, status=status)
            session.add(job)
            session.flush()
            session.add(OutboxEvent(job_id=job.id, created_at=start+timedelta(minutes=index)))
            ids.append(str(job.id))
        # Recovery must not move an older submission ahead of a newer job or duplicate it.
        session.add(OutboxEvent(job_id=ids[0], created_at=start+timedelta(days=1)))
    return ids


def test_dashboard_pagination_and_submission_order(client, test_engine):
    ids = seed_jobs(test_engine)
    first = client.get("/dashboard/data", params={"page_size": 2}).json()
    second = client.get("/dashboard/data", params={"page_size": 2, "page": 2}).json()
    assert first["total"] == second["total"] == 4
    assert [job["id"] for job in first["items"]+second["items"]] == list(reversed(ids))
    assert first["counts"] == {"pending": 1, "running": 1, "completed": 1, "failed": 1}
    assert set(first["items"][0]) == {"id", "name", "status", "job_type", "attempt_count", "created_at"}
    assert client.get("/dashboard/data?page=9").json()["items"] == []
    assert all(set(job) == {"id", "name", "status"} for job in client.get("/jobs").json())


def test_dashboard_search_and_status_keep_global_counts(client, test_engine):
    seed_jobs(test_engine)
    result = client.get("/dashboard/data", params={"q": "sAlEs", "status": "running"}).json()
    assert result["total"] == 1
    assert result["items"][0]["name"] == "Sales South"
    assert sum(result["counts"].values()) == 4
    assert client.get("/dashboard/data?q=absent").json()["total"] == 0


@pytest.mark.parametrize("query", ["%", "_", "100% real_"])
def test_dashboard_search_treats_wildcards_as_literal(client, test_engine, query):
    seed_jobs(test_engine)
    result = client.get("/dashboard/data", params={"q": query}).json()
    assert result["total"] == 1
    assert result["items"][0]["name"] == "100% real_sales"


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101", "status=unknown", "page=1000001", "q="+"x"*201])
def test_dashboard_rejects_invalid_query(client, query):
    assert client.get("/dashboard/data?"+query).status_code == 422


def test_dashboard_empty_and_historical_jobs(client, test_engine):
    empty = client.get("/dashboard/data").json()
    assert empty["items"] == [] and empty["total"] == 0
    assert empty["counts"] == {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    with Session(test_engine) as session, session.begin():
        session.add(Job(name="Historical job"))
    result = client.get("/dashboard/data").json()
    assert result["items"][0]["created_at"] is None


def test_dashboard_assets_and_sample_pipeline(client, test_engine, monkeypatch):
    import tasks
    monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "/assets/dashboard.js?v=4" in page.text
    for path, media_type in [("dashboard.css", "text/css"), ("dashboard-theme.css", "text/css"), ("dashboard.js", "javascript"), ("theme.js", "javascript"), ("column-tools.js", "javascript")]:
        asset = client.get("/assets/"+path)
        assert asset.status_code == 200 and media_type in asset.headers["content-type"]
    assert client.get("/assets/missing.js").status_code == 404
    sample = client.get("/samples/sales.csv")
    assert sample.status_code == 200
    created = client.post("/jobs/csv", files={"file": ("sales.csv", sample.content)}, data={"name":"One-click sample"})
    assert created.status_code == 200
    job_id = created.json()["id"]
    tasks.process_job.run(job_id)
    item = client.get("/dashboard/data").json()["items"][0]
    assert item["id"] == job_id and item["status"] == "completed" and item["attempt_count"] == 1
    result = client.get(f"/jobs/{job_id}/analysis").json()
    assert result["results"]["row_count"] == 4
    assert result["results"]["columns"][1]["numeric"]["mean"] == "401.25"
