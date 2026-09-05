from app.core.config import get_settings
from app.prompts.registry import PromptRegistry


def test_all_character_prompts_exist() -> None:
    registry = PromptRegistry(get_settings().prompts_path)
    names = {item.name for item in registry.list()}
    assert {
        "fahes_generate_quiz",
        "khota_generate_plan",
        "rasheed_recommend",
        "kholasa_summarize",
        "sada_cleanup_transcript",
    } <= names


def test_prompt_rendering_keeps_arabic() -> None:
    registry = PromptRegistry(get_settings().prompts_path)
    prompt = registry.get("fahes_generate_quiz")
    rendered = prompt.render_user(task_parameters="اختبار", source_context="[S1] مصدر")
    assert "اختبار" in rendered
    assert "[S1]" in rendered
