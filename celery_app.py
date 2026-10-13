"""Celery configuration for standalone demonstration tasks."""

import os
from pathlib import Path

from celery import Celery
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

celery_app = Celery("flowforge", broker=redis_url, backend=redis_url, include=["tasks"])
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,
)
