from pathlib import Path

import pytest

from app.core.config import get_settings
from app.prompts.registry import PromptRegistry


def test_all_character_prompts_exist() -> None:
    registry = PromptRegistry(get_settings().prompts_path)
    names = {item.name for item in registry.list()}
    assert {
        "fahes_generate_quiz",
        "khota_generate_plan",
        "khota_extract_topics",
        "rasheed_recommend",
        "kholasa_summarize",
        "sada_cleanup_transcript",
    } <= names


def test_prompt_rendering_keeps_arabic() -> None:
    registry = PromptRegistry(get_settings().prompts_path)
    prompt = registry.get("fahes_generate_quiz")
    rendered = prompt.render_user(
        task_parameters="اختبار",
        source_context="[S1] مصدر",
        learner_instructions="ركّز على الوحدة الثانية",
    )
    assert "اختبار" in rendered
    assert "[S1]" in rendered
    assert "الوحدة الثانية" in rendered


def test_prompt_rendering_rejects_missing_required_value() -> None:
    registry = PromptRegistry(get_settings().prompts_path)
    prompt = registry.get("fahes_generate_quiz")

    with pytest.raises(KeyError, match="source_context"):
        prompt.render_user(task_parameters="اختبار", learner_instructions="لا توجد.")


def test_all_templates_render_without_unresolved_variables() -> None:
    registry = PromptRegistry(get_settings().prompts_path)
    values = {
        "task_parameters": {"language": "en"},
        "source_context": "[S1] trusted source",
        "backend_constraints": {"daily_available_minutes": 60},
        "priority_scores": {"algebra": 1.0},
        "deterministic_plan": [{"date": "2026-09-17", "tasks": []}],
        "authority_data": {"attempts": 1},
        "raw_transcript": "A complete transcript.",
        "segment_timeline": [{"start": 0, "end": 1, "speaker": None}],
        "learner_instructions": "None.",
    }

    for prompt in registry.list():
        rendered = prompt.render_user(**values)
        assert "$" not in rendered, prompt.name


def test_registry_rejects_duplicate_prompt_names(tmp_path: Path) -> None:
    template = """\
name: duplicate
version: \"1.0.0\"
task_type: test
system_prompt: system
user_template: user
"""
    (tmp_path / "one.yaml").write_text(template, encoding="utf-8")
    (tmp_path / "two.yaml").write_text(template, encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate prompt name"):
        PromptRegistry(tmp_path)


def test_registry_rejects_invalid_semantic_version(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text(
        """\
name: invalid_version
version: latest
task_type: test
system_prompt: system
user_template: user
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid semantic version"):
        PromptRegistry(tmp_path)
