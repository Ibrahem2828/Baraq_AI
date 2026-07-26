from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "baraq_ai",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.workers.tasks.process_ai_job": {"queue": "ai_default"},
        "app.workers.tasks.cleanup_expired_data": {"queue": "ai_default"},
    },
    beat_schedule={
        "cleanup-expired-ai-data-daily": {
            "task": "app.workers.tasks.cleanup_expired_data",
            "schedule": 86400.0,
        }
    },
)
