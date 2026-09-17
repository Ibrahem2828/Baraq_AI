from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

import yaml

from app.core.config import get_settings
from app.core.errors import NotFoundError


@dataclass(frozen=True, slots=True)
class PromptSpec:
    name: str
    version: str
    task_type: str
    system_prompt: str
    user_template: str
    metadata: dict[str, Any]
    checksum: str

    def render_user(self, **values: Any) -> str:
        safe_values = {
            key: value if isinstance(value, str) else yaml.safe_dump(value, allow_unicode=True)
            for key, value in values.items()
        }
        # Missing prompt inputs are programming/configuration errors.  Leaving
        # an unresolved ``$variable`` in a production model request is both a
        # quality problem and an easy way to weaken grounding guarantees.
        return Template(self.user_template).substitute(safe_values)


class PromptRegistry:
    def __init__(self, templates_path: Path) -> None:
        self.templates_path = templates_path
        self._prompts: dict[str, PromptSpec] = {}
        self.reload()

    def reload(self) -> None:
        prompts: dict[str, PromptSpec] = {}
        for path in sorted(self.templates_path.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            required = {"name", "version", "task_type", "system_prompt", "user_template"}
            missing = required - set(raw)
            if missing:
                raise ValueError(f"Prompt {path.name} is missing fields: {sorted(missing)}")
            name = str(raw["name"]).strip()
            version = str(raw["version"]).strip()
            task_type = str(raw["task_type"]).strip()
            if not name or not task_type:
                raise ValueError(f"Prompt {path.name} has an empty name or task_type")
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ValueError(f"Prompt {path.name} has an invalid semantic version: {version}")
            if name in prompts:
                raise ValueError(f"Duplicate prompt name '{name}' in {path.name}")
            canonical = path.read_bytes()
            spec = PromptSpec(
                name=name,
                version=version,
                task_type=task_type,
                system_prompt=str(raw["system_prompt"]).strip(),
                user_template=str(raw["user_template"]).strip(),
                metadata=dict(raw.get("metadata") or {}),
                checksum=hashlib.sha256(canonical).hexdigest(),
            )
            prompts[spec.name] = spec
        self._prompts = prompts

    def get(self, name: str) -> PromptSpec:
        try:
            return self._prompts[name]
        except KeyError as exc:
            raise NotFoundError(f"Prompt '{name}' is not registered") from exc

    def list(self) -> list[PromptSpec]:
        return list(self._prompts.values())


@lru_cache(maxsize=1)
def get_prompt_registry() -> PromptRegistry:
    return PromptRegistry(get_settings().prompts_path)
