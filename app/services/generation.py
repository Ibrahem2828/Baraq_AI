from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ProviderError
from app.core.telemetry import AI_COST_USD, AI_PROVIDER_ATTEMPTS, AI_PROVIDER_LATENCY
from app.models.ai_job import AIJob, ProviderAttempt
from app.models.enums import ProviderAttemptStatus
from app.prompts.registry import PromptSpec
from app.providers.base import ProviderResult
from app.providers.capabilities import Capability
from app.providers.router import ProviderRouter
from app.services.provider_budget import ProviderBudgetService
from app.services.routing_config import TaskRouting


class StructuredGenerationService:
    def __init__(self, *, session: AsyncSession, router: ProviderRouter) -> None:
        self.session = session
        self.router = router
        self.budget = ProviderBudgetService(session)

    async def generate(
        self,
        *,
        job: AIJob,
        routing: TaskRouting,
        prompt: PromptSpec,
        user_input: str,
        output_model: type[BaseModel],
    ) -> ProviderResult:
        candidates = await self.router.ordered_candidates(
            routing,
            str(job.idempotency_hash),
            allow_fallback=job.allow_fallback,
            preferred_tier=job.model_tier,
        )
        last_error: Exception | None = None
        attempt_number = len(job.attempts)
        for candidate in candidates:
            if candidate.instance is None:
                continue
            if not await self.budget.has_budget(candidate.account_id):
                continue
            for _ in range(max(1, self.router.settings.provider_max_retries + 1)):
                attempt_number += 1
                attempt = ProviderAttempt(
                    job_id=job.id,
                    attempt_number=attempt_number,
                    provider_account=candidate.account_id,
                    model_name=candidate.model,
                    request_id=job.request_id,
                    status=ProviderAttemptStatus.STARTED,
                )
                self.session.add(attempt)
                await self.session.flush()
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(candidate.timeout_seconds):
                        result = await candidate.instance.generate_structured(
                            model=candidate.model,
                            instructions=prompt.system_prompt,
                            user_input=user_input,
                            output_model=output_model,
                            schema_name=prompt.name,
                            max_output_tokens=candidate.max_output_tokens,
                            reasoning_effort=candidate.reasoning_policy,
                            metadata={
                                "baraq_job_id": str(job.id),
                                "task_type": job.task_type.value,
                                "prompt_version": prompt.version,
                            },
                        )
                    attempt.status = ProviderAttemptStatus.SUCCEEDED
                    attempt.provider_response_id = result.response_id
                    attempt.latency_ms = result.latency_ms
                    attempt.input_tokens = result.usage.input_tokens
                    attempt.output_tokens = result.usage.output_tokens
                    attempt.estimated_cost_usd = result.estimated_cost_usd
                    await self.router.circuit.record_success(candidate.account_id)
                    await self.budget.record(result)
                    account_label = candidate.account_id.value
                    AI_PROVIDER_ATTEMPTS.labels(account_label, candidate.model, "succeeded").inc()
                    AI_PROVIDER_LATENCY.labels(account_label, candidate.model).observe(
                        result.latency_ms / 1000
                    )
                    AI_COST_USD.labels(account_label, candidate.model).inc(
                        result.estimated_cost_usd
                    )
                    await self.session.flush()
                    return result
                except AppError as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    attempt.status = ProviderAttemptStatus.FAILED
                    attempt.error_code = exc.code
                    attempt.error_message = exc.message[:4000]
                    attempt.retryable = exc.retryable
                    attempt.latency_ms = elapsed_ms
                    await self.session.flush()
                    AI_PROVIDER_ATTEMPTS.labels(
                        candidate.account_id.value, candidate.model, "failed"
                    ).inc()
                    last_error = exc
                    if exc.retryable and job.allow_fallback:
                        await self.router.circuit.record_failure(candidate.account_id)
                        continue
                    raise
                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    attempt.status = ProviderAttemptStatus.FAILED
                    attempt.error_code = exc.__class__.__name__
                    attempt.error_message = str(exc)[:4000]
                    attempt.retryable = True
                    attempt.latency_ms = elapsed_ms
                    await self.session.flush()
                    await self.router.circuit.record_failure(candidate.account_id)
                    last_error = exc
                    if job.allow_fallback:
                        continue
                    raise
                finally:
                    await self.session.commit()
        if isinstance(last_error, AppError):
            raise last_error
        raise ProviderError(
            "All configured AI provider accounts failed",
            code="all_providers_failed",
            retryable=True,
        ) from last_error

    async def moderate(self, *, routing_key: str, texts: list[str]) -> list[dict[str, Any]]:
        """Screen generated output text through OpenAI's free moderation
        endpoint before it is persisted or shown to a student. A moderation
        outage or unavailable provider must never block content delivery --
        it only skips this extra safety net for that job, same as any other
        best-effort safety check layered on top of the domain schema
        validation that already gates every output."""
        candidates_texts = [text[:4000] for text in texts if text and text.strip()][:40]
        if not candidates_texts:
            return []
        try:
            candidates = await self.router.candidates_for_capability(
                Capability.MODERATION, routing_key, allow_fallback=False
            )
        except ProviderError:
            return []
        instance = candidates[0].instance
        if instance is None:
            return []
        try:
            results = await instance.moderate(
                model=self.router.settings.openai_moderation_model, inputs=candidates_texts
            )
        except AppError:
            return []
        flags: list[dict[str, Any]] = []
        for text, result in zip(candidates_texts, results, strict=False):
            if not result.get("flagged"):
                continue
            categories = result.get("categories") or {}
            flags.append(
                {
                    "flagged": True,
                    "categories": sorted(name for name, hit in categories.items() if hit),
                    "text_preview": text[:120],
                }
            )
        return flags
