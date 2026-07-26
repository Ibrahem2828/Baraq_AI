from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    cors_origins: list[str] = Field(default_factory=list)

    database_url: str = "postgresql+asyncpg://baraq_ai:change_me@localhost:5432/baraq_ai"
    database_sync_url: str = "postgresql+psycopg://baraq_ai:change_me@localhost:5432/baraq_ai"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    baraq_backend_base_url: str = "https://api.barraq.xn--mgbaab0cxheq.tech"
    # Django is the only public-facing identity and authorization authority.
    # JWT/JWKS settings were removed from the AI production request path in Phase 1.
    baraq_service_id: str = "baraq-ai-service"
    baraq_django_service_id: str = "baraq-django"
    baraq_service_hmac_secret: SecretStr = SecretStr("change-me")
    baraq_callback_timeout_seconds: int = 15
    baraq_http_timeout_seconds: int = 30

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
    openai_transcription_model: str = "gpt-4o-transcribe"
    openai_moderation_model: str = "omni-moderation-latest"
    openai_store_responses: bool = False
    openai_request_timeout_seconds: int = 300

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
    ai_requests_per_minute: int = 20
    job_retention_days: int = 180
    raw_content_retention_days: int = 30
    training_consent_required: bool = True

    sentry_dsn: str | None = None
    prometheus_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    enable_legacy_public_api: bool = False
    enable_admin_api: bool = False

    model_routing_config_path: Path = BASE_DIR / "config" / "model_routing.yaml"
    pricing_config_path: Path = BASE_DIR / "config" / "pricing.yaml"
    prompts_path: Path = BASE_DIR / "app" / "prompts" / "templates"

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def parse_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("public_api_prefix", "internal_api_prefix")
    @classmethod
    def normalize_prefix(cls, value: str) -> str:
        value = "/" + value.strip("/")
        return value.rstrip("/")

    def validate_production(self) -> None:
        if self.app_env != "production":
            return
        if self.debug:
            raise ValueError("DEBUG must be false in production")
        if self.baraq_service_hmac_secret.get_secret_value() in {"", "change-me"}:
            raise ValueError("BARAQ_SERVICE_HMAC_SECRET must be configured")
        if not self.openai_primary_api_key.get_secret_value():
            raise ValueError("OPENAI_PRIMARY_API_KEY is required in production")
        if not self.baraq_backend_base_url.startswith("https://"):
            raise ValueError("BARAQ_BACKEND_BASE_URL must use HTTPS in production")



@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production()
    return settings
