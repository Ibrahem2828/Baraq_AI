"""Exercise the real Django and FastAPI HMAC V2 implementations together.

This is intentionally a no-network validation: it imports each service's
signer/verifier and proves that their canonical protocol works in both
directions using an isolated development configuration.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DJANGO_ROOT = ROOT.parent / "Baraaq_back" / "backend"
TEST_SECRET = "test-hmac-secret"
KEYRING = '{"django-current":"test-hmac-secret"}'


@dataclass(frozen=True)
class SignedRequest:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes

    def get_full_path(self) -> str:
        return self.target


def configure_isolated_environment() -> None:
    os.environ.update(
        {
            "ENVIRONMENT": "development",
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DEBUG": "True",
            "PUBLIC_API_BASE_URL": "http://testserver",
            "ALLOWED_HOSTS": '["localhost"]',
            "CORS_ORIGINS": "[]",
            "DATABASE_URL": "sqlite:///:memory:",
            "REDIS_URL": "locmem://",
            "BARAQ_SERVICE_ID": "baraq-django",
            "BARAQ_DJANGO_SERVICE_ID": "baraq-django",
            "BARAQ_HMAC_CURRENT_KEY_ID": "django-current",
            "BARAQ_HMAC_KEYS_JSON": KEYRING,
            "BARAQ_HMAC_ALLOWED_SERVICES": "baraq-ai-service",
            "BARAQ_HMAC_MAX_CLOCK_SKEW_SECONDS": "300",
            "BARAQ_HMAC_NONCE_TTL_SECONDS": "600",
        }
    )


def main() -> int:
    if not DJANGO_ROOT.is_dir():
        raise SystemExit(f"Django repository not found at {DJANGO_ROOT}")
    configure_isolated_environment()
    sys.path.insert(0, str(DJANGO_ROOT))

    django = import_module("django")
    django.setup()
    from app.core.config import get_settings
    from app.core.security import make_service_signature as ai_sign
    from app.core.security import verify_service_signature as ai_verify

    django_security = import_module("apps.ai_integration.security")
    django_sign = django_security.make_service_signature
    django_verify = django_security.verify_internal_request

    get_settings.cache_clear()
    timestamp = int(time.time())
    body = b'{"a":1}'
    django_target = "/api/ai/v1/jobs?b=2&a=1"
    django_headers = django_sign(
        method="POST",
        target=django_target,
        body=body,
        timestamp=timestamp,
        nonce="django-to-ai-nonce-0001",
        service="baraq-django",
        key_id="django-current",
    )
    ai_verify(
        method="POST",
        target=django_target,
        body=body,
        headers=django_headers,
        now=timestamp,
    )

    ai_target = "/api/internal/v1/ai/sources/7/manifest/?user_id=3"
    ai_headers = ai_sign(
        method="GET",
        target=ai_target,
        body=b"",
        timestamp=timestamp,
        nonce="ai-to-django-nonce-0001",
        service="baraq-ai-service",
        key_id="django-current",
    )
    django_verify(SignedRequest("GET", ai_target, ai_headers, b""))
    print("HMAC V2 Django <-> AI interop: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
