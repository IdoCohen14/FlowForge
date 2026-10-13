from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base, get_database_url
from models import Job


def test_database_url_uses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "test_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test@password:%")
    monkeypatch.setenv("POSTGRES_DB", "test_database")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5433")

    url = get_database_url()

    assert url.drivername == "postgresql+psycopg"
    assert url.username == "test_user"
    assert url.password == "test@password:%"
    assert url.database == "test_database"
    assert url.host == "localhost"
    assert url.port == 5433


def test_job_model_persists_with_defaults() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            job = Job(name="Process report")
            session.add(job)
            session.commit()
            job_id = job.id
        with Session(engine) as session:
            stored = session.get(Job, job_id)
            assert stored is not None
            assert isinstance(stored.id, UUID)
            assert stored.name == "Process report"
            assert stored.status == "pending"
    finally:
        engine.dispose()
