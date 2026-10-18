"""Celery delivery configuration and isolated-verification namespaces."""

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
    worker_prefetch_multiplier=1,
    task_default_queue=os.getenv("FLOWFORGE_QUEUE", "celery"),
    result_backend_transport_options={"global_keyprefix": os.getenv("FLOWFORGE_REDIS_PREFIX", ""), "visibility_timeout": 360},
    visibility_timeout=360,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=5,
    broker_transport_options={"visibility_timeout": 360, "global_keyprefix": os.getenv("FLOWFORGE_REDIS_PREFIX", ""), "socket_timeout": 5, "socket_connect_timeout": 5},
)
