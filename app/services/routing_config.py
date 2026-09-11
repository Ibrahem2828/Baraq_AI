from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models.enums import Provider, QualityTier


@dataclass(frozen=True, slots=True)
class RoutingCandidateSpec:
    provider: Provider
    quality_tier: QualityTier
    priority: int


@dataclass(frozen=True, slots=True)
class TaskRouting:
    task_type: str
    prompt: str | None
    max_output_tokens: int
    reasoning_effort: str | None
    timeout_seconds: int
    candidates: list[RoutingCandidateSpec]


class RoutingConfig:
    def __init__(self, path: Path) -> None:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.version = int(raw.get("version", 1))
        self.tasks = raw.get("tasks", {})

    def get(self, task_type: str) -> TaskRouting:
        try:
            raw = self.tasks[task_type]
        except KeyError as exc:
            raise NotFoundError(f"No routing configuration for task '{task_type}'") from exc
        candidates = [
            RoutingCandidateSpec(
                provider=Provider(item["provider"]),
                quality_tier=QualityTier(item.get("quality_tier", "balanced")),
                priority=int(item.get("priority", index * 10)),
            )
            for index, item in enumerate(raw.get("candidates", []), start=1)
        ]
        if not candidates:
            raise ValueError(f"Task '{task_type}' has no routing candidates configured")
        return TaskRouting(
            task_type=task_type,
            prompt=raw.get("prompt"),
            max_output_tokens=int(raw.get("max_output_tokens", 8000)),
            reasoning_effort=raw.get("reasoning_effort"),
            timeout_seconds=int(raw.get("timeout_seconds", 120)),
            candidates=sorted(candidates, key=lambda item: item.priority),
        )


@lru_cache(maxsize=1)
def get_routing_config() -> RoutingConfig:
    return RoutingConfig(get_settings().model_routing_config_path)
