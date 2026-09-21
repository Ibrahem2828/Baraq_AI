from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

from app.core.config import get_settings
from app.core.logging import get_logger
from app.workers.async_runner import close_runner, get_runner

settings = get_settings()
logger = get_logger(__name__)


# celery-stubs leaves Signal.connect untyped.
@worker_process_init.connect  # type: ignore[untyped-decorator]
def _init_worker_process(**_: object) -> None:
    """Create this prefork child's one persistent event loop right after
    fork, before any task runs. This is the primary lifecycle hook (see
    app/workers/async_runner.py's docstring for the full failure this
    replaces); async_runner.run() would still create the loop lazily on
    first use if this signal never fired, but doing it here means the
    loop -- and the AsyncEngine that will be created on it -- exist from
    the start of the child's working life, not from whichever task happens
    to run first.
    """
    get_runner()
    logger.info("ai_worker_process_initialized")


# celery-stubs leaves Signal.connect untyped.
@worker_process_shutdown.connect  # type: ignore[untyped-decorator]
def _shutdown_worker_process(**_: object) -> None:
    """Dispose this child's engine and close its event loop before the
    process actually exits. Ordering matters: this must run before the
    interpreter starts tearing down, or `AsyncEngine.dispose()` may not get
    a live loop to run on at all."""
    close_runner()


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
    task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
    task_time_limit=settings.celery_task_time_limit_seconds,
    # `process_ai_job`'s static route below is only the fallback used if it's
    # ever sent without an explicit queue kwarg -- in practice every real
    # dispatch goes through `app.workers.tasks._enqueue_outbox_event`, which
    # always passes `queue=event.target_queue` (ai_interactive for
    # fahes/khota/rasheed/kholasa, ai_audio for Sada transcription -- see
    # `app.models.enums.TASK_QUEUE`), so this overrides the static route.
    #
    # `ai_ingestion` (declared on the worker's `--queues=` list in
    # compose.yaml) has no task routed to it today: source ingestion
    # (extraction/chunking/embeddings) runs synchronously inside
    # `process_ai_job` itself, not as a separate Celery task. Left declared,
    # not removed, so a future dedicated ingestion task can be added without
    # a worker-topology change -- but nothing is silently faked here to fill
    # it in the meantime.
    task_routes={
        "app.workers.tasks.process_ai_job": {"queue": "ai_interactive"},
        "app.workers.tasks.deliver_result_webhook": {"queue": "ai_background"},
        "app.workers.tasks.dispatch_job_outbox": {"queue": "ai_background"},
        "app.workers.tasks.cleanup_expired_data": {"queue": "ai_background"},
        "app.workers.tasks.recover_stale_ai_jobs": {"queue": "ai_background"},
    },
    beat_schedule={
        "dispatch-durable-ai-jobs": {
            "task": "app.workers.tasks.dispatch_job_outbox",
            "schedule": 10.0,
        },
        "cleanup-expired-ai-data-daily": {
            "task": "app.workers.tasks.cleanup_expired_data",
            "schedule": 86400.0,
        },
        "recover-stale-ai-jobs": {
            "task": "app.workers.tasks.recover_stale_ai_jobs",
            "schedule": 60.0,
        },
    },
)
