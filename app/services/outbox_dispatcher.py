from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from kombu.exceptions import (
    OperationalError as KombuOperationalError,
)
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.ai_job import JobDispatchOutboxEvent
from app.models.enums import DispatchOutboxStatus

logger = get_logger(__name__)


class OutboxDispatcher:
    """Claim durable outbox rows and hand them to Celery at least once."""

    async def recover_stale_locks(self) -> int:
        settings = get_settings()
        stale_before = datetime.now(UTC) - timedelta(seconds=settings.outbox_lock_timeout_seconds)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                update(JobDispatchOutboxEvent)
                .where(
                    JobDispatchOutboxEvent.status == DispatchOutboxStatus.DISPATCHING,
                    JobDispatchOutboxEvent.locked_at < stale_before,
                )
                .values(status=DispatchOutboxStatus.PENDING, locked_at=None)
            )
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    async def claim_pending(self) -> list[JobDispatchOutboxEvent]:
        settings = get_settings()
        now = datetime.now(UTC)
        async with AsyncSessionLocal() as session:
            events = list(
                (
                    await session.scalars(
                        select(JobDispatchOutboxEvent)
                        .where(
                            JobDispatchOutboxEvent.status == DispatchOutboxStatus.PENDING,
                            JobDispatchOutboxEvent.available_at <= now,
                        )
                        .order_by(JobDispatchOutboxEvent.created_at)
                        .limit(settings.outbox_dispatch_batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for event in events:
                event.status = DispatchOutboxStatus.DISPATCHING
                event.locked_at = now
                event.attempt_count += 1
            await session.commit()
            return events

    async def mark_dispatched(self, event_id: object) -> None:
        async with AsyncSessionLocal() as session:
            event = await session.get(JobDispatchOutboxEvent, event_id, with_for_update=True)
            if event is None:
                return
            event.status = DispatchOutboxStatus.DISPATCHED
            event.dispatched_at = datetime.now(UTC)
            event.locked_at = None
            event.last_error = None
            await session.commit()

    async def release_for_retry(self, event_id: object, error: Exception) -> None:
        settings = get_settings()
        async with AsyncSessionLocal() as session:
            event = await session.get(JobDispatchOutboxEvent, event_id, with_for_update=True)
            if event is None:
                return
            delay_seconds = min(
                2 ** min(event.attempt_count, 16), settings.outbox_retry_max_seconds
            )
            event.status = DispatchOutboxStatus.PENDING
            event.available_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            event.locked_at = None
            event.last_error = error.__class__.__name__[:200]
            await session.commit()
            logger.warning(
                "ai_outbox_retry_scheduled",
                event_id=str(event_id),
                request_id=event.request_id,
                event_type=event.event_type,
            )

    async def dispatch_pending(self, enqueue: Callable[[JobDispatchOutboxEvent], object]) -> int:
        """Publish a claimed event to its explicit Celery handler.

        ``process_ai_job`` is complete once the broker accepts it.  Result
        webhook events remain locked until their handler receives Django's
        acknowledgement, so an AI result can never be lost after generation.
        """
        await self.recover_stale_locks()
        events = await self.claim_pending()
        delivered = 0
        for event in events:
            try:
                enqueue(event)
            except (ConnectionError, OSError, TimeoutError, KombuOperationalError) as exc:
                await self.release_for_retry(event.id, exc)
            else:
                if event.event_type == "process_ai_job":
                    await self.mark_dispatched(event.id)
                logger.info(
                    "ai_outbox_dispatched",
                    event_id=str(event.id),
                    request_id=event.request_id,
                    event_type=event.event_type,
                )
                delivered += 1
        return delivered
