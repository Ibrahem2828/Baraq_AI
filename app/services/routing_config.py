from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_settings
from app.core.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class TaskRouting:
    task_type: str
    model_tier: str
    prompt: str | None
    max_output_tokens: int
    reasoning_effort: str | None
    timeout_seconds: int
    provider_order: list[str]


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
        return TaskRouting(
            task_type=task_type,
            model_tier=str(raw.get("model_tier", "balanced")),
            prompt=raw.get("prompt"),
            max_output_tokens=int(raw.get("max_output_tokens", 8000)),
            reasoning_effort=raw.get("reasoning_effort"),
            timeout_seconds=int(raw.get("timeout_seconds", 120)),
            provider_order=list(raw.get("provider_order", [])),
        )


@lru_cache(maxsize=1)
def get_routing_config() -> RoutingConfig:
    return RoutingConfig(get_settings().model_routing_config_path)
