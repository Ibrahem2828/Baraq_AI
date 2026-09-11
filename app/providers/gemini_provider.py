"""Google Gemini provider adapter.

Implements the same :class:`LLMProvider` interface as OpenAI so the router,
``StructuredGenerationService`` and every validator downstream are provider
agnostic. Capabilities this adapter does not implement (transcription,
moderation) are declared unsupported in ``app.providers.capabilities`` so the
router never routes a task here expecting them, and the methods below raise a
typed, honest error rather than silently degrading.
"""

from __future__ import annotations

import time
from typing import Any, cast

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError

from app.core.errors import ProviderError, ValidationFailure, classify_provider_status
from app.models.enums import Provider, ProviderAccount
from app.providers.base import LLMProvider, ProviderResult, ProviderUsage, TranscriptionResult
from app.services.cost import get_cost_calculator


class GeminiProvider(LLMProvider):
    account = ProviderAccount.GEMINI_PRIMARY
    provider_family = Provider.GEMINI

    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: int) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                base_url=base_url,
                timeout=timeout_seconds * 1000,
            ),
        )

    @staticmethod
    def _status_from_exc(exc: Exception) -> int | None:
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        return int(status) if isinstance(status, (int, str)) and str(status).isdigit() else None

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
        del schema_name
        started = time.perf_counter()
        config = genai_types.GenerateContentConfig(
            system_instruction=instructions,
            response_mime_type="application/json",
            response_schema=output_model,
            max_output_tokens=max_output_tokens,
            labels=metadata,
        )
        if reasoning_effort:
            # Gemini's thinking budget is a token count, not a named tier; a
            # coarse mapping keeps the router's tier vocabulary uniform.
            budget = {"low": 0, "medium": 4096, "high": 16384}.get(reasoning_effort)
            if budget is not None:
                config.thinking_config = genai_types.ThinkingConfig(thinking_budget=budget)
        try:
            response = await self._client.aio.models.generate_content(
                model=model, contents=user_input, config=config
            )
            raw_text = response.text
            if not raw_text:
                raise ProviderError(
                    "Gemini returned no output text",
                    code="empty_provider_output",
                    retryable=True,
                )
            validated = output_model.model_validate_json(raw_text)
        except ValidationError as exc:
            raise ValidationFailure(
                "Structured output did not pass the domain schema",
                details={"errors": exc.errors(include_url=False)},
            ) from exc
        except ValidationFailure:
            raise
        except ProviderError:
            raise
        except Exception as exc:  # SDK exceptions are normalized here.
            status = self._status_from_exc(exc)
            _, retryable = classify_provider_status(status)
            raise ProviderError(
                "Gemini request failed",
                code=getattr(exc, "code", None) or exc.__class__.__name__,
                retryable=retryable,
                details={"http_status": status},
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage_meta = response.usage_metadata
        usage = ProviderUsage(
            input_tokens=int(getattr(usage_meta, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage_meta, "candidates_token_count", 0) or 0),
            total_tokens=int(getattr(usage_meta, "total_token_count", 0) or 0),
            cached_input_tokens=int(getattr(usage_meta, "cached_content_token_count", 0) or 0),
        )
        cost = get_cost_calculator().token_cost(model, usage)
        return ProviderResult(
            data=validated.model_dump(mode="json"),
            account=self.account,
            model=model,
            response_id=getattr(response, "response_id", None),
            usage=usage,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            metadata={"finish_reason": str(getattr(response, "finish_reason", None))},
        )

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            # list[str] is not a subtype of the SDK's list[str | Image | ...]
            # union under mypy's invariant list typing, even though every
            # element here really is a str -- cast is type-level only, the
            # runtime value is unchanged.
            contents = cast("list[str | Any]", texts)
            response = await self._client.aio.models.embed_content(model=model, contents=contents)
        except Exception as exc:
            status = self._status_from_exc(exc)
            _, retryable = classify_provider_status(status)
            raise ProviderError(
                "Gemini embeddings request failed",
                code=getattr(exc, "code", None) or exc.__class__.__name__,
                retryable=retryable,
                details={"http_status": status},
            ) from exc
        return [list(item.values or []) for item in response.embeddings or []]

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
        del model, filename, content, language, prompt, diarize
        raise ProviderError(
            "Gemini transcription is not an approved capability for this provider yet",
            code="gemini_transcription_unsupported",
            retryable=False,
        )

    async def moderate(self, *, model: str, inputs: list[str]) -> list[dict[str, Any]]:
        del model, inputs
        raise ProviderError(
            "Gemini moderation is not an approved capability for this provider yet",
            code="gemini_moderation_unsupported",
            retryable=False,
        )
