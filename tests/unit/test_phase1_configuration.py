from __future__ import annotations

import pytest

from app.core.config import BASE_DIR, Settings


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "debug": False,
        "provider_mode": "live",
        "allowed_hosts": ["ai.example.test"],
        "database_url": "postgresql+asyncpg://baraq:strong-password@db.example.test/baraq",
        "database_sync_url": "postgresql+psycopg://baraq:strong-password@db.example.test/baraq",
        "redis_url": "redis://:redis-password@redis.example.test:6379/0",
        "celery_broker_url": "redis://:redis-password@redis.example.test:6379/1",
        "celery_result_backend": "redis://:redis-password@redis.example.test:6379/2",
        "baraq_backend_base_url": "https://backend.example.test",
        "openai_primary_api_key": "test-key",
        "baraq_hmac_current_key_id": "current",
        "baraq_hmac_keys_json": '{"current":"a-realistic-test-secret-0123456789ab"}',
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_production_configuration_accepts_non_placeholder_keyring() -> None:
    production_settings().validate_production()


def test_production_configuration_allows_http_only_for_the_explicit_private_backend() -> None:
    production_settings(
        baraq_backend_base_url="http://backend:8000",
        baraq_backend_allow_insecure_http=True,
    ).validate_production()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.baraqapp.com",
        "http://backend.example.test",
        "ftp://backend",
    ],
)
def test_production_configuration_rejects_non_private_http_backend(base_url: str) -> None:
    with pytest.raises(ValueError):
        production_settings(
            baraq_backend_base_url=base_url,
            baraq_backend_allow_insecure_http=True,
        ).validate_production()


@pytest.mark.parametrize(
    "overrides",
    [
        {"debug": True},
        {"baraq_hmac_keys_json": '{"current":"local-development-key-not-for-production"}'},
        {"allowed_hosts": ["*"]},
        {"database_url": "postgresql+asyncpg://baraq:change_me@db.example.test/baraq"},
        {"provider_mode": "mock"},
        {"enable_ai_lab": True},
        {"baraq_runtime_mode": "lab"},
        {"baraq_hmac_keys_json": '{"current":"too-short-secret"}'},
        {"database_sync_url": "postgresql+psycopg://baraq:change_me@db.example.test/baraq"},
        {"redis_url": "memory://"},
        {"celery_broker_url": "amqp://broker.example.test"},
        {"celery_result_backend": "redis:///0"},
    ],
)
def test_production_configuration_fails_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        production_settings(**overrides).validate_production()


def test_allowed_hosts_parses_the_csv_format_env_vars_actually_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: pydantic-settings tries to json.loads() a raw env
    string for any list-typed field before parse_csv() (a "before"
    validator) ever runs, unless the field is annotated with NoDecode.
    Constructing Settings via model_validate()/kwargs (as production_settings()
    above does) never exercises this path -- only real env vars do, which is
    exactly how every deployment and .env.example actually configure this."""
    monkeypatch.setenv("ALLOWED_HOSTS", "localhost,127.0.0.1,ai.example.test")
    monkeypatch.setenv("CORS_ORIGINS", "https://dashboard.example.test,https://app.example.test")

    settings = Settings(_env_file=None)

    assert settings.allowed_hosts == ["localhost", "127.0.0.1", "ai.example.test"]
    assert settings.cors_origins == ["https://dashboard.example.test", "https://app.example.test"]


def test_settings_boot_from_a_literal_copy_of_env_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """.env.example is the literal onboarding path any new deployment or
    engineer follows -- if Settings can't parse it as-is, the service is
    broken for everyone who follows the documented setup exactly."""
    env_example = BASE_DIR / ".env.example"
    assert env_example.exists()

    for line in env_example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        monkeypatch.setenv(key.strip(), value.strip())

    settings = Settings(_env_file=None)
    assert isinstance(settings.allowed_hosts, list)
    assert len(settings.allowed_hosts) > 0


@pytest.mark.parametrize(
    "missing",
    [
        "database_url",
        "database_sync_url",
        "redis_url",
        "celery_broker_url",
        "celery_result_backend",
        "baraq_backend_base_url",
        "allowed_hosts",
        "baraq_hmac_keys_json",
        "baraq_hmac_current_key_id",
    ],
)
def test_production_requires_every_connection_setting_to_be_written_down(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default would start the service half-configured (localhost Redis, a
    public backend hostname, ...) and only fail on the first request."""
    values = {
        "app_env": "production",
        "debug": False,
        "provider_mode": "live",
        "allowed_hosts": ["ai.example.test"],
        "database_url": "postgresql+asyncpg://baraq:strong-password@db.example.test/baraq",
        "database_sync_url": "postgresql+psycopg://baraq:strong-password@db.example.test/baraq",
        "redis_url": "redis://:redis-password@redis.example.test:6379/0",
        "celery_broker_url": "redis://:redis-password@redis.example.test:6379/1",
        "celery_result_backend": "redis://:redis-password@redis.example.test:6379/2",
        "baraq_backend_base_url": "https://backend.example.test",
        "openai_primary_api_key": "test-key",
        "baraq_hmac_current_key_id": "current",
        "baraq_hmac_keys_json": '{"current":"a-realistic-test-secret-0123456789ab"}',
    }
    del values[missing]
    # CI exports these for real; without clearing them (and the .env file) the
    # "missing" setting would be picked up from the environment.
    for name in values:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.delenv(missing.upper(), raising=False)
    with pytest.raises(ValueError, match=missing.upper()):
        Settings(_env_file=None, **values).validate_production()  # type: ignore[arg-type]


def test_all_characters_are_enabled_by_default() -> None:
    assert Settings().enabled_characters == ["fahes", "khota", "rasheed", "kholasa", "sada"]


def test_enabled_characters_parse_the_csv_format_env_vars_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLED_CHARACTERS", " Rasheed , fahes,khota,rasheed")
    assert Settings().enabled_characters == ["rasheed", "fahes", "khota"]


@pytest.mark.parametrize("value", ["", "rasheed,nobody", "kholasa;sada"])
def test_enabled_characters_reject_empty_or_unknown_names(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ENABLED_CHARACTERS", value)
    with pytest.raises(ValueError):
        Settings()
