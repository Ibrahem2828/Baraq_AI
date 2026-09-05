"""Release gate for Baraq AI.

Static checks always run. Runtime checks deliberately fail closed until an
operator supplies disposable infrastructure and a real provider smoke command.
Nothing in this script substitutes fakes for a production acceptance gate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    command: list[str] | None
    required: bool = True


def run(check: Check, *, environment: dict[str, str] | None = None) -> bool:
    if check.command is None:
        print(f"{check.name}: NOT RUN")
        return not check.required
    try:
        result = subprocess.run(check.command, cwd=ROOT, check=False, env=environment)
        passed = result.returncode == 0
    except FileNotFoundError as exc:
        # e.g. docker/alembic not on PATH in this environment -- a real gate
        # failure, but it should read as one instead of crashing the script
        # and hiding every check still queued after it.
        print(f"{check.name}: FAIL (command not found: {exc.filename})")
        return not check.required
    label = "PASS" if passed else ("FAIL" if check.required else "FAIL (advisory, non-blocking)")
    print(f"{check.name}: {label}")
    return passed or not check.required


def openapi_is_current() -> bool:
    # The public contract is the service-mode API surface, never whatever a
    # developer's local .env happens to set (BARAQ_RUNTIME_MODE=lab silently
    # swaps in the unrelated Lab router and made this check pass against the
    # wrong schema before this explicit override existed).
    os.environ["BARAQ_RUNTIME_MODE"] = "service"
    from app.main import app

    path = ROOT / "docs" / "openapi.json"
    try:
        checked_in = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(checked_in == app.openapi())


def command_from_environment(name: str) -> list[str] | None:
    raw = os.environ.get(name, "").strip()
    return raw.split() if raw else None


def pricing_is_ready_for_live_mode() -> bool:
    """Only enforced once PROVIDER_MODE=live -- at A1 nothing is spending
    real money yet, so a pending price is a documented gap, not a failure
    (spec section 22 applies to *active* models)."""
    import yaml

    from app.core.config import get_settings
    from app.providers.model_aliases import model_for_tier, transcription_model_for
    from app.services.routing_config import get_routing_config

    settings = get_settings()
    if settings.provider_mode != "live":
        print("pricing_validation: SKIPPED (PROVIDER_MODE is not live)")
        return True
    pricing = yaml.safe_load(settings.pricing_config_path.read_text(encoding="utf-8")) or {}
    models = pricing.get("models", {})
    unpriced: list[str] = []
    for task_type in ("fahes_generate_quiz", "khota_generate_plan", "rasheed_recommendations",
                       "kholasa_generate_summary", "sada_transcribe_audio"):
        routing = get_routing_config().get(task_type)
        for candidate in routing.candidates:
            # pricing.yaml keys by real model name (e.g. "gpt-5.1"), never by
            # the provider-neutral quality tier ("high_quality") -- resolve
            # the same way app/services/generation.py actually does before
            # pricing a candidate, or every candidate looks unpriced
            # regardless of what's really in pricing.yaml. This is the
            # cleanup/generation model for every task including Sada (its
            # separate speech-to-text step is capability-routed, not driven
            # by model_routing.yaml -- see model_for_tier vs
            # transcription_model_for below).
            model_name = model_for_tier(settings, candidate.provider, candidate.quality_tier.value)
            entry = models.get(model_name, {})
            if entry.get("status") == "pending_verification" or not entry:
                unpriced.append(f"{task_type}:{candidate.provider.value}:{model_name}")

    # Sada's actual audio transcription step (as opposed to its text cleanup
    # step above) is resolved by capability routing
    # (candidates_for_capability(Capability.TRANSCRIPTION) in
    # app/pipelines/sada.py), not model_routing.yaml, and only OpenAI is ever
    # registered for that capability (app/providers/router.py) -- Gemini can
    # never actually be selected for it, so pricing a placeholder Gemini
    # transcription model name would just be permanent, unfixable noise here.
    from app.models.enums import Provider

    transcription_model = transcription_model_for(settings, Provider.OPENAI)
    entry = models.get(transcription_model, {})
    if entry.get("status") == "pending_verification" or not entry:
        unpriced.append(f"sada_transcribe_audio:openai:{transcription_model}")
    if unpriced:
        print(f"pricing_validation: FAIL (unpriced active candidates: {unpriced})")
        return False
    print("pricing_validation: PASS")
    return True


def main() -> int:
    environment = os.environ.copy()
    # Deterministic regardless of a developer's local .env (e.g. a leftover
    # BARAQ_RUNTIME_MODE=lab or a decommissioned provider key) -- the release
    # gate must reflect the checked-in service configuration, not ambient
    # workstation state.
    environment["BARAQ_RUNTIME_MODE"] = "service"
    environment.setdefault("PROVIDER_MODE", "mock")
    static_checks = [
        Check("compile", [sys.executable, "-m", "compileall", "app", "alembic", "scripts"]),
        Check("ruff", [sys.executable, "-m", "ruff", "check", "."]),
        Check("mypy", [sys.executable, "-m", "mypy", "."]),
        Check("pytest", [sys.executable, "-m", "pytest"]),
        Check("package_and_secret_scan", [sys.executable, "scripts/validate_package.py"]),
        Check(
            "django_hmac_interop",
            [sys.executable, "scripts/validate_django_hmac_interop.py"],
        ),
        Check(
            "compose_config",
            ["docker", "compose", "config", "--quiet", "--no-env-resolution", "--no-interpolate"],
        ),
        Check("seed_evals", [sys.executable, "scripts/run_evals.py", "--provider-mode", "mock"],
              required=False),
    ]
    # Materialize every result before reducing with all() -- all() on a bare
    # generator short-circuits at the first falsy value, silently skipping
    # (never even running) every later check in this same invocation.
    static_results = [run(check, environment=environment) for check in static_checks]
    passed = all(static_results)
    openapi_ok = openapi_is_current()
    print(f"openapi_drift: {'PASS' if openapi_ok else 'FAIL'}")
    passed = passed and openapi_ok
    pricing_ok = pricing_is_ready_for_live_mode()
    passed = passed and pricing_ok

    database_url = os.environ.get("AI_RELEASE_TEST_DATABASE_SYNC_URL")
    migration_environment = environment.copy()
    if database_url:
        migration_environment["DATABASE_SYNC_URL"] = database_url
    runtime_checks = [
        Check(
            "fresh_postgresql_migration",
            ["alembic", "upgrade", "head"] if database_url else None,
        ),
        Check(
            "docker_build",
            ["docker", "build", "-t", "baraq-ai-release-gate", "."]
            if os.environ.get("AI_RELEASE_RUN_DOCKER") == "1"
            else None,
        ),
        Check(
            "redis_celery_durable_e2e",
            command_from_environment("AI_RELEASE_RUNTIME_E2E_COMMAND"),
        ),
        Check("live_provider_smoke", command_from_environment("AI_RELEASE_LIVE_PROVIDER_COMMAND")),
    ]
    for check in runtime_checks:
        passed = run(check, environment=migration_environment) and passed

    print(f"AI SECTION RESULT: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
