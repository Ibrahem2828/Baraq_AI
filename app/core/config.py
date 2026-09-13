from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production", "test"] = "development"
    app_name: str = "Baraq AI Service"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"
    public_api_prefix: str = "/api/ai/v1"
    internal_api_prefix: str = "/internal/v1"
    # NoDecode: without it, pydantic-settings tries to json.loads() the raw
    # env value for any list-typed field before parse_csv below ever runs --
    # crashing on exactly the CSV format .env.example documents
    # ("localhost,127.0.0.1,...", not '["localhost", ...]').
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    database_url: str = "postgresql+asyncpg://baraq_ai:change_me@localhost:5432/baraq_ai"
    database_sync_url: str = "postgresql+psycopg://baraq_ai:change_me@localhost:5432/baraq_ai"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    baraq_backend_base_url: str = "https://api.barraq.xn--mgbaab0cxheq.tech"
    # Production normally requires HTTPS.  A deployment may opt in to an
    # HTTP callback only for the private Docker service hostname below.
    # This keeps public/internet HTTP impossible while allowing the AI
    # worker to reach Django without traversing Caddy.
    baraq_backend_allow_insecure_http: bool = False
    # Django is the only public-facing identity and authorization authority.
    # JWT/JWKS settings were removed from the AI production request path in Phase 1.
    baraq_service_id: str = "baraq-ai-service"
    baraq_django_service_id: str = "baraq-django"
    # HMAC V2 keyring.  The JSON value is intentionally a string so `.env`
    # parsing remains predictable across Docker, Coolify and local shells.
    baraq_hmac_current_key_id: str = "django-dev-1"
    baraq_hmac_keys_json: str = '{"django-dev-1":"local-development-key-not-for-production"}'
    hmac_max_clock_skew_seconds: int = Field(default=300, ge=1, le=3600)
    hmac_nonce_ttl_seconds: int = Field(default=600, ge=1, le=7200)
    hmac_v1_compat_enabled: bool = False
    # Retained only so an explicit, time-bounded V1 compatibility deployment
    # can be configured without accepting it by default.
    baraq_service_hmac_secret: SecretStr = SecretStr("")
    baraq_callback_timeout_seconds: int = 15
    baraq_http_timeout_seconds: int = 30
    outbox_dispatch_batch_size: int = Field(default=20, ge=1, le=200)
    outbox_lock_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    outbox_retry_max_seconds: int = Field(default=300, ge=1, le=3600)

    openai_primary_enabled: bool = True
    openai_primary_api_key: SecretStr = SecretStr("")
    openai_primary_project_id: str | None = None
    openai_primary_organization_id: str | None = None
    openai_primary_base_url: str = "https://api.openai.com/v1"
    openai_primary_monthly_budget_usd: float = 50.0

    openai_secondary_enabled: bool = False
    openai_secondary_api_key: SecretStr = SecretStr("")
    openai_secondary_project_id: str | None = None
    openai_secondary_organization_id: str | None = None
    openai_secondary_base_url: str = "https://api.openai.com/v1"
    openai_secondary_monthly_budget_usd: float = 50.0

    openai_model_high_quality: str = "gpt-5.1"
    openai_model_balanced: str = "gpt-5-mini"
    openai_model_fast: str = "gpt-5-nano"
    openai_embedding_model: str = "text-embedding-3-small"
    # gpt-4o-mini-transcribe is half the cost of gpt-4o-transcribe ($0.003 vs
    # $0.006/minute) with comparable quality for clear classroom audio; Sada
    # only escalates to gpt-4o-transcribe-diarize when a job explicitly
    # requests diarization (see app/pipelines/sada.py), so this default is
    # the cost floor for the common case.
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_moderation_model: str = "omni-moderation-latest"
    openai_store_responses: bool = False
    openai_request_timeout_seconds: int = 300

    gemini_primary_enabled: bool = False
    gemini_primary_api_key: SecretStr = SecretStr("")
    gemini_primary_base_url: str = "https://generativelanguage.googleapis.com"
    gemini_primary_monthly_budget_usd: float = 50.0
    gemini_model_high_quality: str = "gemini-2.5-pro"
    gemini_model_balanced: str = "gemini-2.5-flash"
    gemini_model_fast: str = "gemini-2.5-flash-lite"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_request_timeout_seconds: int = 300

    # Single explicit switch for the whole provider layer (spec: one variable,
    # not scattered `if debug` branches). `mock`/`replay` never call a real
    # network endpoint; `live` requires real Gemini/OpenAI credentials.
    provider_mode: Literal["mock", "replay", "live"] = "mock"
    replay_fixtures_dir: Path = BASE_DIR / "tests" / "fixtures" / "replay"

    provider_routing_policy: Literal["failover", "weighted"] = "failover"
    openai_primary_weight: int = 80
    openai_secondary_weight: int = 20
    provider_max_retries: int = 2
    provider_cooldown_seconds: int = 90
    provider_circuit_failure_threshold: int = 5

    embedding_dimensions: int = 1536
    rag_chunk_size_chars: int = 2200
    rag_chunk_overlap_chars: int = 300
    rag_top_k: int = 10
    rag_rerank_top_k: int = 6
    rag_max_context_chars: int = 22000
    rag_min_similarity: float = 0.20

    max_source_file_bytes: int = 50 * 1024 * 1024
    max_audio_file_bytes: int = 200 * 1024 * 1024
    max_audio_seconds: int = 7200
    max_request_text_chars: int = 100000
    # Blueprint 02_AI_PLATFORM.md §3.1 (Layer 0 Trust Gate): every inbound
    # request body must be checked against a size limit before any AI
    # processing -- source/audio *content* never travels this path (the
    # service pulls that itself from Django), so this bounds the JSON job
    # envelope only, independent of whatever a reverse proxy in front of
    # this service may or may not enforce.
    max_request_body_bytes: int = 1 * 1024 * 1024
    # Blueprint 02_AI_PLATFORM.md §8.2/§8.3: chunk a long recording into
    # ~10-minute pieces cut near a silence boundary, with a small overlap so
    # a word right at the cut isn't lost. Audio at or under
    # sada_chunk_threshold_seconds is transcribed as a single call (today's
    # existing, unchanged, well-tested path) -- chunking only kicks in once
    # a recording is meaningfully longer than one target chunk.
    sada_chunk_target_seconds: int = 600
    sada_chunk_threshold_seconds: int = 900
    sada_chunk_overlap_seconds: int = 5
    sada_chunk_silence_search_window_seconds: int = 30
    sada_chunk_min_silence_len_ms: int = 700
    sada_chunk_silence_thresh_dbfs: int = -40
    sada_max_concurrent_chunks: int = 3
    ai_requests_per_minute: int = 20
    job_retention_days: int = 180
    raw_content_retention_days: int = 30
    training_candidate_retention_days: int = 365
    job_stale_after_seconds: int = 900
    job_max_recoveries: int = 2
    training_consent_required: bool = True

    sentry_dsn: str | None = None
    prometheus_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    enable_legacy_public_api: bool = False
    enable_admin_api: bool = False
    enable_ai_lab: bool = False
    baraq_runtime_mode: Literal["service", "lab"] = "service"
    lab_host: str = "127.0.0.1"
    lab_port: int = Field(default=8000, ge=1, le=65535)
    lab_storage_dir: Path = BASE_DIR / ".baraq_lab"
    lab_max_upload_mb: int = Field(default=100, ge=1, le=500)
    lab_max_text_chars: int = Field(default=2_000_000, ge=10_000, le=10_000_000)
    lab_max_output_tokens: int = Field(default=8_000, ge=256, le=64_000)

    sada_stt_provider: Literal["local_whisper"] = "local_whisper"
    sada_local_whisper_model: str = "small"
    sada_language: str = "ar"

    model_routing_config_path: Path = BASE_DIR / "config" / "model_routing.yaml"
    pricing_config_path: Path = BASE_DIR / "config" / "pricing.yaml"
    prompts_path: Path = BASE_DIR / "app" / "prompts" / "templates"

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def parse_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value: object) -> object:
        # Some process managers export DEBUG=release.  Treat that explicit
        # deployment convention as false instead of failing before /health.
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "false", "0", "no", "off"}:
                return False
            if normalized in {"true", "1", "yes", "on", "development"}:
                return True
        return value

    @field_validator("public_api_prefix", "internal_api_prefix")
    @classmethod
    def normalize_prefix(cls, value: str) -> str:
        value = "/" + value.strip("/")
        return value.rstrip("/")

    def validate_production(self) -> None:
        if self.app_env != "production":
            return
        if self.baraq_runtime_mode == "lab":
            raise ValueError("BARAQ_RUNTIME_MODE=lab is not permitted in production")
        if self.enable_ai_lab:
            raise ValueError("ENABLE_AI_LAB must be false in production")
        if self.debug:
            raise ValueError("DEBUG must be false in production")
        if self.provider_mode != "live":
            raise ValueError("PROVIDER_MODE must be 'live' in production")
        keys = self.hmac_keyring
        if self.baraq_hmac_current_key_id not in keys or any(
            value in {"", "change-me", "local-development-key-not-for-production"}
            for value in keys.values()
        ):
            raise ValueError("BARAQ_HMAC_KEYS_JSON must contain non-placeholder production keys")
        if not self.openai_primary_api_key.get_secret_value():
            raise ValueError("OPENAI_PRIMARY_API_KEY is required in production")
        parsed_backend_url = urlparse(self.baraq_backend_base_url)
        allows_private_backend_http = (
            self.baraq_backend_allow_insecure_http
            and parsed_backend_url.scheme == "http"
            and parsed_backend_url.hostname == "backend"
        )
        if parsed_backend_url.scheme != "https" and not allows_private_backend_http:
            raise ValueError(
                "BARAQ_BACKEND_BASE_URL must use HTTPS in production unless it is the "
                "explicitly enabled private Docker backend service"
            )
        if "*" in self.allowed_hosts or not self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must contain explicit production hosts")
        parsed_database_url = urlparse(self.database_url)
        if not parsed_database_url.hostname or any(
            placeholder in self.database_url
            for placeholder in ("change_me", "change-me", "local_development_only")
        ):
            raise ValueError("DATABASE_URL must not use a placeholder in production")

    @property
    def hmac_keyring(self) -> dict[str, str]:
        """Return configured HMAC V2 keys without ever logging their values."""
        try:
            raw_keys = json.loads(self.baraq_hmac_keys_json)
        except json.JSONDecodeError as exc:
            raise ValueError("BARAQ_HMAC_KEYS_JSON must be a JSON object") from exc
        if not isinstance(raw_keys, dict) or not raw_keys:
            raise ValueError("BARAQ_HMAC_KEYS_JSON must contain at least one key")
        keys: dict[str, str] = {}
        for key_id, secret in raw_keys.items():
            if (
                not isinstance(key_id, str)
                or not key_id
                or not isinstance(secret, str)
                or not secret
            ):
                raise ValueError("BARAQ_HMAC_KEYS_JSON has an invalid key entry")
            keys[key_id] = secret
        return keys


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production()
    return settings
