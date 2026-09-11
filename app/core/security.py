from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from app.core.config import get_settings
from app.core.errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class AuthenticatedDjangoService:
    """Marker returned only after an authenticated Django-to-AI request."""

    service_id: str
    key_id: str


HMAC_V2_SCHEME = "BARAQ-HMAC-V2"
REQUIRED_HMAC_HEADERS = (
    "x-baraq-service",
    "x-baraq-key-id",
    "x-baraq-timestamp",
    "x-baraq-nonce",
    "x-content-sha256",
    "x-baraq-signature",
)


def canonical_request_target(target: str) -> str:
    """Canonicalise a relative target while preserving its trailing slash.

    Query pairs are decoded once, sorted by key then value (including repeated
    keys), and re-encoded with RFC 3986 unreserved characters left literal.
    An empty query is represented by the path only.
    """
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        raise ValueError("canonical request target must be an absolute path")
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    if not pairs:
        return parsed.path
    encoded_query = urlencode(sorted(pairs), doseq=True, quote_via=quote, safe="-._~")
    return f"{parsed.path}?{encoded_query}"


def canonical_request(
    *,
    service: str,
    key_id: str,
    timestamp: int,
    nonce: str,
    method: str,
    target: str,
    body_sha256: str,
) -> bytes:
    return "\n".join(
        (
            HMAC_V2_SCHEME,
            service,
            key_id,
            str(timestamp),
            nonce,
            method.upper(),
            canonical_request_target(target),
            body_sha256,
        )
    ).encode("utf-8")


def _secret_for_key_id(key_id: str) -> str:
    try:
        return get_settings().hmac_keyring[key_id]
    except KeyError as exc:
        raise AuthenticationError("Unknown HMAC key", code="unknown_key_id") from exc


def _signature(*, secret: str, canonical: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def make_service_signature(
    *,
    method: str,
    target: str,
    body: bytes,
    timestamp: int | None = None,
    nonce: str | None = None,
    service: str | None = None,
    key_id: str | None = None,
) -> dict[str, str]:
    """Sign an internal request using the single Baraq HMAC V2 protocol."""
    settings = get_settings()
    timestamp = int(time.time()) if timestamp is None else timestamp
    service = service or settings.baraq_service_id
    key_id = key_id or settings.baraq_hmac_current_key_id
    nonce = nonce or secrets.token_urlsafe(24)
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = canonical_request(
        service=service,
        key_id=key_id,
        timestamp=timestamp,
        nonce=nonce,
        method=method,
        target=target,
        body_sha256=body_hash,
    )
    signature = _signature(secret=_secret_for_key_id(key_id), canonical=canonical)
    return {
        "X-Baraq-Service": service,
        "X-Baraq-Key-Id": key_id,
        "X-Baraq-Timestamp": str(timestamp),
        "X-Baraq-Nonce": nonce,
        "X-Baraq-Signature": signature,
        "X-Content-SHA256": body_hash,
    }


def verify_service_signature(
    *,
    method: str,
    target: str,
    body: bytes,
    headers: Mapping[str, str],
    now: int | None = None,
) -> AuthenticatedDjangoService:
    """Verify immutable request components before the Redis replay write."""
    settings = get_settings()
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    try:
        service_id, key_id, timestamp_raw, nonce, received_body_hash, received = (
            normalized_headers[name] for name in REQUIRED_HMAC_HEADERS
        )
    except (KeyError, ValueError) as exc:
        raise AuthenticationError(
            "Required HMAC V2 headers are missing", code="missing_signature_headers"
        ) from exc
    try:
        timestamp = int(timestamp_raw)
    except ValueError as exc:
        raise AuthenticationError("HMAC timestamp is invalid", code="invalid_timestamp") from exc
    if service_id != settings.baraq_django_service_id:
        raise AuthenticationError("Calling service is not authorised", code="unauthorized_service")
    if len(nonce) < 16 or len(nonce) > 128:
        raise AuthenticationError("HMAC nonce is invalid", code="invalid_nonce")
    expected_body_hash = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(received_body_hash, expected_body_hash):
        raise AuthenticationError(
            "Request content checksum is invalid", code="invalid_content_hash"
        )
    if (
        abs((int(time.time()) if now is None else now) - timestamp)
        > settings.hmac_max_clock_skew_seconds
    ):
        raise AuthenticationError(
            "HMAC timestamp is outside the allowed window", code="expired_signature"
        )
    if len(received) != 64 or any(
        character not in "0123456789abcdefABCDEF" for character in received
    ):
        raise AuthenticationError("HMAC signature is malformed", code="invalid_signature")
    expected = _signature(
        secret=_secret_for_key_id(key_id),
        canonical=canonical_request(
            service=service_id,
            key_id=key_id,
            timestamp=timestamp,
            nonce=nonce,
            method=method,
            target=target,
            body_sha256=expected_body_hash,
        ),
    )
    if not hmac.compare_digest(received, expected):
        raise AuthenticationError("HMAC signature is invalid", code="invalid_signature")
    return AuthenticatedDjangoService(service_id=service_id, key_id=key_id)


async def verify_and_consume_service_signature(
    *, method: str, target: str, body: bytes, headers: Mapping[str, str], redis: Any
) -> AuthenticatedDjangoService:
    """Verify HMAC V2 and atomically reserve its nonce for replay protection."""
    authenticated = verify_service_signature(
        method=method, target=target, body=body, headers=headers
    )
    normalized_headers = {key.lower(): value for key, value in headers.items()}
    nonce = normalized_headers["x-baraq-nonce"]
    key = f"baraq:hmac:nonce:{authenticated.service_id}:{nonce}"
    accepted = await redis.set(key, "1", nx=True, ex=get_settings().hmac_nonce_ttl_seconds)
    if not accepted:
        raise AuthenticationError("HMAC nonce was already used", code="replay_detected")
    return authenticated


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
