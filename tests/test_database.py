from uuid import UUID, uuid4
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from database import get_database_url
from models import CsvAnalysis, Job


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


def test_job_model_persists_with_defaults(test_engine: Engine) -> None:
    with Session(test_engine) as session:
        job = Job(name="Process report")
        session.add(job)
        session.commit()
        job_id = job.id
    with Session(test_engine) as session:
        stored = session.get(Job, job_id)
        assert stored is not None
        assert isinstance(stored.id, UUID)
        assert stored.name == "Process report"
        assert stored.status == "pending"
        assert stored.job_type == "demo"
        assert stored.csv_analysis is None


def test_csv_analysis_relationship_and_results(test_engine: Engine) -> None:
    with Session(test_engine) as session, session.begin():
        job = Job(name="CSV job", job_type="csv")
        job.csv_analysis = CsvAnalysis(storage_filename="generated.csv")
        session.add(job)
        session.flush()
        job_id = job.id
    with Session(test_engine) as session, session.begin():
        analysis = session.get(CsvAnalysis, job_id)
        assert analysis.job.job_type == "csv"
        assert analysis.job.csv_analysis is analysis
        assert analysis.results is None
        assert analysis.error_message is None
        analysis.results = {"row_count": 3, "columns": [{"mean": 2.5}]}
        analysis.error_message = "Example error"
    with Session(test_engine) as session:
        analysis = session.get(CsvAnalysis, job_id)
        assert analysis.results == {"row_count": 3, "columns": [{"mean": 2.5}]}
        assert analysis.error_message == "Example error"


@pytest.mark.parametrize("violation", ["foreign-key", "filename", "duplicate", "job-type"])
def test_csv_schema_constraints(test_engine: Engine, violation: str) -> None:
    with Session(test_engine) as session, session.begin():
        job = Job(name="CSV", job_type="csv")
        session.add(job)
        session.flush()
        job_id = job.id
        if violation == "duplicate":
            session.add(CsvAnalysis(job_id=job_id, storage_filename="first.csv"))
    with Session(test_engine) as session:
        if violation == "job-type":
            session.add(Job(name="Invalid", job_type="unknown"))
        else:
            session.add(CsvAnalysis(
                job_id=uuid4() if violation == "foreign-key" else job_id,
                storage_filename=None if violation == "filename" else "second.csv",
            ))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.parametrize("test_engine", ["0003_outbox_retry_schedule"], indirect=True)
def test_csv_migration_preserves_existing_records(test_engine: Engine) -> None:
    job_id, event_id = uuid4(), uuid4()
    with test_engine.begin() as connection:
        connection.execute(text("INSERT INTO jobs (id, name, status) VALUES (:id, 'Existing', 'completed')"), {"id": job_id})
        connection.execute(text("INSERT INTO outbox_events (id, job_id) VALUES (:id, :job_id)"), {"id": event_id, "job_id": job_id})
        before = connection.execute(text("SELECT * FROM outbox_events")).mappings().all()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with test_engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        stored = connection.execute(text("SELECT name, status, job_type FROM jobs WHERE id = :id"), {"id": job_id}).one()
        assert tuple(stored) == ("Existing", "completed", "demo")
        assert connection.execute(text("SELECT * FROM outbox_events")).mappings().all() == before
