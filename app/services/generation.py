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
from app.providers.router import ProviderRouter
from app.services.routing_config import TaskRouting
from app.services.provider_budget import ProviderBudgetService


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
        model = self.router.model_for_tier(job.model_tier or routing.model_tier)
        candidates = await self.router.ordered_candidates(str(job.idempotency_hash))
        if routing.provider_order:
            allowed = {name for name in routing.provider_order}
            candidates = [item for item in candidates if item.account.value in allowed]
            if self.router.settings.provider_routing_policy == "failover":
                order = {name: index for index, name in enumerate(routing.provider_order)}
                candidates.sort(key=lambda item: order.get(item.account.value, len(order)))
        last_error: Exception | None = None
        attempt_number = len(job.attempts)
        for candidate in candidates:
            if not await self.budget.has_budget(candidate.account):
                continue
            for _ in range(max(1, self.router.settings.provider_max_retries + 1)):
                attempt_number += 1
                attempt = ProviderAttempt(
                    job_id=job.id,
                    attempt_number=attempt_number,
                    provider_account=candidate.account,
                    model_name=model,
                    status=ProviderAttemptStatus.STARTED,
                )
                self.session.add(attempt)
                await self.session.flush()
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(routing.timeout_seconds):
                        result = await candidate.provider.generate_structured(
                            model=model,
                            instructions=prompt.system_prompt,
                            user_input=user_input,
                            output_model=output_model,
                            schema_name=prompt.name,
                            max_output_tokens=routing.max_output_tokens,
                            reasoning_effort=routing.reasoning_effort,
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
                    await self.router.circuit.record_success(candidate.account)
                    await self.budget.record(result)
                    AI_PROVIDER_ATTEMPTS.labels(
                        candidate.account.value, model, "succeeded"
                    ).inc()
                    AI_PROVIDER_LATENCY.labels(candidate.account.value, model).observe(
                        result.latency_ms / 1000
                    )
                    AI_COST_USD.labels(candidate.account.value, model).inc(
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
                    AI_PROVIDER_ATTEMPTS.labels(candidate.account.value, model, "failed").inc()
                    last_error = exc
                    if exc.retryable:
                        await self.router.circuit.record_failure(candidate.account)
                        continue
                    raise
                except Exception as exc:  # noqa: BLE001
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    attempt.status = ProviderAttemptStatus.FAILED
                    attempt.error_code = exc.__class__.__name__
                    attempt.error_message = str(exc)[:4000]
                    attempt.retryable = True
                    attempt.latency_ms = elapsed_ms
                    await self.session.flush()
                    await self.router.circuit.record_failure(candidate.account)
                    last_error = exc
                    continue
                finally:
                    await self.session.commit()
        if isinstance(last_error, AppError):
            raise last_error
        raise ProviderError(
            "All configured AI provider accounts failed",
            code="all_providers_failed",
            retryable=True,
        ) from last_error
