from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from app.core.config import get_settings
from app.core.errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: str
    role: str
    email: str | None = None
    token_id: str | None = None


@lru_cache(maxsize=1)
def get_jwk_client() -> PyJWKClient:
    settings = get_settings()
    return PyJWKClient(settings.baraq_backend_jwks_url, cache_keys=True, lifespan=300)


def decode_access_token(token: str) -> AuthenticatedUser:
    settings = get_settings()
    try:
        signing_key = get_jwk_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=settings.baraq_backend_audience,
            issuer=settings.baraq_backend_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except Exception as exc:  # noqa: BLE001
        raise AuthenticationError("Invalid or expired access token") from exc

    user_id = str(payload.get("sub") or payload.get("user_id") or "")
    if not user_id:
        raise AuthenticationError("Access token has no user identifier")
    return AuthenticatedUser(
        user_id=user_id,
        role=str(payload.get("role") or "student"),
        email=payload.get("email"),
        token_id=payload.get("jti"),
    )


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
        timestamp = int(headers["x-baraq-timestamp"])
        received = headers["x-baraq-signature"]
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Missing service signature") from exc
    if abs(int(time.time()) - timestamp) > max_skew_seconds:
        raise AuthenticationError("Expired service signature")
    expected = make_service_signature(method=method, path=path, body=body, timestamp=timestamp)[
        "X-Baraq-Signature"
    ]
    if not hmac.compare_digest(received, expected):
        raise AuthenticationError("Invalid service signature")


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
