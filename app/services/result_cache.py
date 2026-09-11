"""Reusable result cache (spec section 4/36): the same effective request --
same tenant, task, validated input + model policy, source content versions,
prompt, and pipeline version -- reuses a previously generated result instead
of paying for another provider call. Distinct from AIJob.idempotency_hash,
which only dedupes an identical (user_id, client_job_id) resubmission: this
keys on *content*, so a different client_job_id (even a different job
entirely) for the same effective request still hits. user_id is part of the
fingerprint, so a hit never crosses tenants -- see compute_fingerprint.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.enums import TaskType
from app.models.result_cache import CachedAIResult
from app.pipelines.base import PipelineResult

# KHOTA_GENERATE_PLAN is deliberately excluded: its output is a day/task
# schedule derived from the *current date* and available time slots
# (app/services/khota_scheduler.py), not purely a function of static input --
# caching it could silently hand back a schedule anchored to a past date.
CACHEABLE_TASK_TYPES = frozenset(
    {
        TaskType.FAHES_GENERATE_QUIZ,
        TaskType.KHOLASA_GENERATE_SUMMARY,
        TaskType.RASHEED_RECOMMENDATIONS,
        TaskType.SADA_TRANSCRIBE_AUDIO,
    }
)


def compute_fingerprint(
    *,
    user_id: str,
    task_type: str,
    input_hash: str,
    source_versions: dict[str, str],
    prompt_checksum: str,
    pipeline_version: str,
) -> str:
    """input_hash already covers task_type + the validated task input +
    model policy (see JobService.create_job/stable_hash) -- it does NOT
    cover source *content*, since it hashes source_ids, not what they
    resolve to, so source_versions (id -> content_sha256, frozen at job
    creation) is included separately. user_id keeps cache entries
    tenant-scoped; prompt_checksum/pipeline_version make a prompt or
    pipeline code change naturally miss the old cache instead of serving a
    stale result under it."""
    canonical = json.dumps(
        {
            "user_id": user_id,
            "task_type": task_type,
            "input_hash": input_hash,
            "source_versions": source_versions,
            "prompt_checksum": prompt_checksum,
            "pipeline_version": pipeline_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResultCacheService:
    CACHEABLE_TASK_TYPES = CACHEABLE_TASK_TYPES

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lookup(self, fingerprint: str) -> CachedAIResult | None:
        cached = await self.session.scalar(
            select(CachedAIResult).where(CachedAIResult.fingerprint == fingerprint)
        )
        if cached is None:
            return None
        cached.hit_count += 1
        cached.last_hit_at = datetime.now(UTC)
        await self.session.flush()
        return cached

    async def store(
        self,
        *,
        fingerprint: str,
        user_id: str,
        task_type: TaskType,
        result: PipelineResult,
    ) -> None:
        """Writes on its own short-lived session/transaction (mirroring
        ProviderBudgetService's reservation methods and OutboxDispatcher),
        not the caller's long-lived job-processing session: a duplicate-
        fingerprint race here must never abort the caller's much larger,
        already-pending transaction (the job's AIOutput, outbox event, and
        status transition), which sharing a session would risk."""
        provider = result.provider_result
        try:
            async with AsyncSessionLocal() as session:
                session.add(
                    CachedAIResult(
                        fingerprint=fingerprint,
                        user_id=user_id,
                        task_type=task_type,
                        result_json=result.result_json,
                        citations=[item.model_dump(mode="json") for item in result.citations],
                        quality_score=result.quality_score,
                        groundedness_score=result.groundedness_score,
                        warnings=result.warnings,
                        security_flags=result.security_flags,
                        source_model_name=provider.model,
                        source_provider_account=provider.account,
                        source_estimated_cost_usd=provider.estimated_cost_usd,
                    )
                )
                await session.commit()
        except IntegrityError:
            # A concurrent job for the exact same content finished and
            # cached first -- keep its entry rather than erroring here.
            pass


def cache_hit_metadata(cached: CachedAIResult) -> dict[str, Any]:
    return {
        "result_cache_hit": True,
        "source_model_name": cached.source_model_name,
        "hit_count": cached.hit_count,
    }
