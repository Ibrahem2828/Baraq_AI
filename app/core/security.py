from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class AuthenticatedDjangoService:
    """Marker returned only after an authenticated Django-to-AI request."""

    service_id: str


def make_service_signature(
    *, method: str, path: str, body: bytes, timestamp: int | None = None
) -> dict[str, str]:
    settings = get_settings()
    timestamp = timestamp or int(time.time())
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}".encode()
    signature = hmac.new(
        settings.baraq_service_hmac_secret.get_secret_value().encode(),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Baraq-Service": settings.baraq_service_id,
        "X-Baraq-Timestamp": str(timestamp),
        "X-Baraq-Signature": signature,
        "X-Content-SHA256": body_hash,
    }


def verify_service_signature(
    *, method: str, path: str, body: bytes, headers: dict[str, str], max_skew_seconds: int = 300
) -> None:
    settings = get_settings()
    try:
        service_id = headers["x-baraq-service"]
        timestamp = int(headers["x-baraq-timestamp"])
        received = headers["x-baraq-signature"]
        received_body_hash = headers["x-content-sha256"]
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Missing service signature") from exc
    if service_id != settings.baraq_django_service_id:
        raise AuthenticationError("Unauthorized calling service")
    expected_body_hash = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(received_body_hash, expected_body_hash):
        raise AuthenticationError("Invalid content checksum")
    if abs(int(time.time()) - timestamp) > max_skew_seconds:
        raise AuthenticationError("Expired service signature")
    expected = make_service_signature(method=method, path=path, body=body, timestamp=timestamp)[
        "X-Baraq-Signature"
    ]
    if not hmac.compare_digest(received, expected):
        raise AuthenticationError("Invalid service signature")


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
