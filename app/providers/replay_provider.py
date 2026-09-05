"""File-based replay provider for integration tests (spec section 6).

Unlike :class:`MockProvider` (fixed in-code payloads for unit-test speed),
Replay loads sanitized JSON fixtures from disk so integration suites can
exercise success *and* the mandatory negative scenarios (schema mismatch,
timeout, rate limit, empty output) without any network call or committed
secret.

Fixture layout::

    tests/fixtures/replay/<task_type>/<scenario>.json
    tests/fixtures/replay/_common/<scenario>.json   # shared negative scenarios

Each fixture is one JSON object with a ``kind`` discriminator:

- ``{"kind": "success", "payload": {...}, "usage": {...}}``
- ``{"kind": "error", "code": "...", "message": "...", "retryable": true}``
- ``{"kind": "invalid_json"}`` -- returns text that model_validate rejects
- ``{"kind": "empty_output"}`` -- simulates an empty provider response
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import ProviderError, ValidationFailure
from app.models.enums import Provider, ProviderAccount
from app.providers.base import LLMProvider, ProviderResult, ProviderUsage, TranscriptionResult

DEFAULT_SCENARIO = "success"


class ReplayProvider(LLMProvider):
    account = ProviderAccount.REPLAY
    provider_family = Provider.REPLAY

    def __init__(self, *, fixtures_dir: Path) -> None:
        self.fixtures_dir = fixtures_dir

    def _load_fixture(self, *, task_type: str, scenario: str) -> dict[str, Any]:
        candidates = [
            self.fixtures_dir / task_type / f"{scenario}.json",
            self.fixtures_dir / "_common" / f"{scenario}.json",
        ]
        for path in candidates:
            if path.exists():
                data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
                return data
        raise ProviderError(
            f"No replay fixture found for task_type='{task_type}' scenario='{scenario}'",
            code="replay_fixture_missing",
            retryable=False,
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
        del instructions, user_input, max_output_tokens, reasoning_effort
        started = time.perf_counter()
        task_type = metadata.get("task_type", schema_name)
        scenario = metadata.get("replay_scenario", DEFAULT_SCENARIO)
        fixture = self._load_fixture(task_type=task_type, scenario=scenario)
        kind = fixture.get("kind")

        if kind == "error":
            raise ProviderError(
                str(fixture.get("message", "Replay fixture error")),
                code=str(fixture.get("code", "replay_error")),
                retryable=bool(fixture.get("retryable", False)),
            )
        if kind == "empty_output":
            raise ProviderError(
                "Replay fixture simulates an empty provider response",
                code="empty_provider_output",
                retryable=True,
            )
        if kind == "invalid_json":
            raise ValidationFailure(
                "Replay fixture simulates invalid provider JSON",
                code="provider_invalid_json",
            )
        if kind != "success":
            raise ProviderError(
                f"Replay fixture has an unknown kind '{kind}'",
                code="replay_fixture_invalid",
                retryable=False,
            )

        payload = fixture.get("payload", {})
        try:
            validated = output_model.model_validate(payload)
        except ValidationError as exc:
            raise ValidationFailure(
                "Replay fixture payload does not match the current domain schema",
                details={"errors": exc.errors(include_url=False)},
            ) from exc

        usage_raw = fixture.get("usage", {})
        usage = ProviderUsage(
            input_tokens=int(usage_raw.get("input_tokens", 0)),
            output_tokens=int(usage_raw.get("output_tokens", 0)),
            total_tokens=int(usage_raw.get("total_tokens", 0)),
            cached_input_tokens=int(usage_raw.get("cached_input_tokens", 0)),
        )
        return ProviderResult(
            data=validated.model_dump(mode="json"),
            account=self.account,
            model=model,
            response_id=f"replay-{task_type}-{scenario}",
            usage=usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
            estimated_cost_usd=0.0,
            metadata={"simulated": True, "provider_mode": "replay", "scenario": scenario},
        )

    async def embed(self, *, model: str, texts: list[str]) -> list[list[float]]:
        del model
        return [[0.0] * 8 for _ in texts]

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
        del filename, content, prompt, diarize
        return TranscriptionResult(
            text="هذا نص تفريغ من مزود Replay.",
            segments=[{"start_seconds": 0.0, "end_seconds": 2.0, "text": "نص Replay"}],
            account=self.account,
            model=model,
            response_id="replay-transcription",
            usage=ProviderUsage(),
            latency_ms=1,
            estimated_cost_usd=0.0,
        )

    async def moderate(self, *, model: str, inputs: list[str]) -> list[dict[str, Any]]:
        del model
        return [{"flagged": False, "categories": {}} for _ in inputs]
