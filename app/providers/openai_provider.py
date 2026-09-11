from __future__ import annotations

import json
import time
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.core.errors import ProviderError, ValidationFailure
from app.models.enums import Provider, ProviderAccount
from app.providers.base import (
    EmbeddingResult,
    LLMProvider,
    ProviderResult,
    ProviderUsage,
    TranscriptionResult,
)
from app.services.cost import get_cost_calculator
from app.utils.json_schema import to_openai_strict_schema


class OpenAIProvider(LLMProvider):
    provider_family = Provider.OPENAI

    def __init__(
        self,
        *,
        account: ProviderAccount,
        api_key: str,
        project_id: str | None,
        organization_id: str | None,
        base_url: str,
        store_responses: bool,
        timeout_seconds: int,
    ) -> None:
        self.account = account
        self.store_responses = store_responses
        self.client = AsyncOpenAI(
            api_key=api_key,
            project=project_id,
            organization=organization_id,
            base_url=base_url,
            timeout=float(timeout_seconds),
            max_retries=0,
        )

    @staticmethod
    def _usage_from_response(response: Any) -> ProviderUsage:
        usage = getattr(response, "usage", None)
        if usage is None:
            return ProviderUsage()
        input_details = getattr(usage, "input_tokens_details", None)
        cached = int(getattr(input_details, "cached_tokens", 0) or 0)
        return ProviderUsage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            cached_input_tokens=cached,
        )

    async def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        user_input: str,
        output_model: type[BaseModel],
        schema_name: str,
        max_output_tokens: int,
        reasoning_effort: str | None,
        metadata: dict[str, str],
    ) -> ProviderResult:
        started = time.perf_counter()
        schema = to_openai_strict_schema(output_model.model_json_schema())
        request: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": [{"role": "user", "content": user_input}],
            "max_output_tokens": max_output_tokens,
            "store": self.store_responses,
            "metadata": metadata,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name[:64],
                    "description": f"Strict output schema for Baraq task {schema_name}",
                    "schema": schema,
                    "strict": True,
                }
            },
        }
        if reasoning_effort:
            request["reasoning"] = {"effort": reasoning_effort}
        try:
            response = await self.client.responses.create(**request)
            raw_text = response.output_text
            if not raw_text:
                raise ProviderError(
                    "OpenAI returned no output text",
                    code="empty_provider_output",
                    retryable=True,
                )
            parsed = json.loads(raw_text)
            validated = output_model.model_validate(parsed)
        except ValidationError as exc:
            raise ValidationFailure(
                "Structured output did not pass the domain schema",
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        except json.JSONDecodeError as exc:
            raise ValidationFailure("Provider output is not valid JSON") from exc
        except ValidationFailure:
            raise
        except Exception as exc:  # SDK exceptions are normalized here.
            status = getattr(exc, "status_code", None)
            retryable = status in {408, 409, 429, 500, 502, 503, 504} or status is None
            code = getattr(exc, "code", None) or exc.__class__.__name__
            raise ProviderError(
                "OpenAI request failed",
                code=str(code),
                retryable=retryable,
                details={"http_status": status},
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = self._usage_from_response(response)
        cost = get_cost_calculator().token_cost(
            model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
        )
        return ProviderResult(
            data=validated.model_dump(mode="json"),
            account=self.account,
            model=model,
            response_id=getattr(response, "id", None),
            usage=usage,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            metadata={"status": getattr(response, "status", None)},
        )

    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], account=self.account, model=model)
        started = time.perf_counter()
        try:
            response = await self.client.embeddings.create(model=model, input=texts)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            raise ProviderError(
                "OpenAI embeddings request failed",
                code=getattr(exc, "code", None) or exc.__class__.__name__,
                retryable=status in {408, 409, 429, 500, 502, 503, 504} or status is None,
                details={"http_status": status},
            ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage_obj = getattr(response, "usage", None)
        input_tokens = int(getattr(usage_obj, "total_tokens", 0) or 0)
        usage = ProviderUsage(input_tokens=input_tokens, total_tokens=input_tokens)
        cost = get_cost_calculator().embedding_cost(model, input_tokens)
        return EmbeddingResult(
            vectors=[item.embedding for item in response.data],
            account=self.account,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
        )

    async def transcribe(
        self,
        *,
        model: str,
        filename: str,
        content: bytes,
        language: str | None,
        prompt: str | None,
        diarize: bool,
    ) -> TranscriptionResult:
        started = time.perf_counter()
        selected_model = "gpt-4o-transcribe-diarize" if diarize else model
        kwargs: dict[str, Any] = {
            "model": selected_model,
            "file": (filename, content),
            "response_format": "verbose_json",
        }
        if language:
            kwargs["language"] = language.split("-")[0]
        if prompt and not diarize:
            kwargs["prompt"] = prompt
        try:
            response = await self.client.audio.transcriptions.create(**kwargs)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            raise ProviderError(
                "OpenAI transcription request failed",
                code=getattr(exc, "code", None) or exc.__class__.__name__,
                retryable=status in {408, 409, 429, 500, 502, 503, 504} or status is None,
                details={"http_status": status},
            ) from exc

        segments: list[dict[str, Any]] = []
        for item in getattr(response, "segments", None) or []:
            if hasattr(item, "model_dump"):
                segments.append(item.model_dump())
            elif isinstance(item, dict):
                segments.append(item)
        usage_obj = getattr(response, "usage", None)
        # verbose_json exposes total audio length as `duration` (seconds); if a
        # future response shape omits it, fall back to the last segment's end
        # time rather than silently billing $0 for real audio minutes.
        duration_seconds = float(getattr(response, "duration", 0.0) or 0.0)
        if duration_seconds <= 0.0 and segments:
            duration_seconds = float(segments[-1].get("end") or 0.0)
        usage = ProviderUsage(
            input_tokens=int(getattr(usage_obj, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_obj, "output_tokens", 0) or 0),
            total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
            audio_seconds=duration_seconds,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        cost = get_cost_calculator().transcription_cost(
            selected_model,
            seconds=duration_seconds,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        return TranscriptionResult(
            text=str(getattr(response, "text", "")),
            segments=segments,
            account=self.account,
            model=selected_model,
            response_id=getattr(response, "id", None),
            usage=usage,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
        )

    async def moderate(self, *, model: str, inputs: list[str]) -> list[dict[str, Any]]:
        if not inputs:
            return []
        try:
            response = await self.client.moderations.create(model=model, input=inputs)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            raise ProviderError(
                "OpenAI moderation request failed",
                code=getattr(exc, "code", None) or exc.__class__.__name__,
                retryable=status in {408, 409, 429, 500, 502, 503, 504} or status is None,
            ) from exc
        results: list[dict[str, Any]] = []
        for result in response.results:
            results.append(result.model_dump(mode="json"))
        return results
