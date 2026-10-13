"""API tests use a new PostgreSQL database per test, never the development DB."""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from database import get_database_url


@pytest.fixture
def test_engine() -> Iterator[Engine]:
    development_url = get_database_url()
    database_name = f"flowforge_test_{uuid4().hex}"
    assert database_name != development_url.database
    admin = create_engine(development_url, isolation_level="AUTOCOMMIT")
    engine = create_engine(development_url.set(database=database_name))
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        created = True
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        if created:
            # This name is generated here, never accepted from external configuration.
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin.dispose()
