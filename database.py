"""Database configuration and request-scoped sessions."""

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


def get_database_url() -> URL:
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise ValueError(f"Missing database configuration: {', '.join(missing)}")
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    )


def get_engine() -> Engine:
    return create_engine(get_database_url(), connect_args={"connect_timeout": 5})


@lru_cache(maxsize=1)
def get_application_engine() -> Engine:
    return get_engine()


def session_scope(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


def get_session() -> Iterator[Session]:
    yield from session_scope(get_application_engine())
