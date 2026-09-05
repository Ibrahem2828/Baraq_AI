from __future__ import annotations

import time
from collections.abc import Callable, Generator

import pytest

from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.core.security import (
    canonical_request,
    canonical_request_target,
    make_service_signature,
    verify_and_consume_service_signature,
    verify_service_signature,
)


@pytest.fixture(autouse=True)
def hmac_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("BARAQ_SERVICE_ID", "baraq-ai-service")
    monkeypatch.setenv("BARAQ_DJANGO_SERVICE_ID", "baraq-django")
    monkeypatch.setenv("BARAQ_HMAC_CURRENT_KEY_ID", "django-current")
    monkeypatch.setenv("BARAQ_HMAC_KEYS_JSON", '{"django-current":"test-hmac-secret"}')
    monkeypatch.setenv("HMAC_MAX_CLOCK_SKEW_SECONDS", "300")
    monkeypatch.setenv("HMAC_NONCE_TTL_SECONDS", "600")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def signed_headers(
    *, body: bytes = b'{"a":1}', target: str = "/api/ai/v1/jobs?a=1"
) -> dict[str, str]:
    return make_service_signature(
        method="POST",
        target=target,
        body=body,
        timestamp=1_700_000_000,
        nonce="nonce-for-test-0001",
        service="baraq-django",
        key_id="django-current",
    )


def test_canonical_target_sorts_and_preserves_repeated_query_parameters() -> None:
    assert canonical_request_target("/a/?z=2&a=x&a=&space=a%20b") == "/a/?a=&a=x&space=a%20b&z=2"


def test_canonical_request_has_fixed_v2_layout() -> None:
    assert canonical_request(
        service="baraq-django",
        key_id="django-current",
        timestamp=1_700_000_000,
        nonce="nonce-for-test-0001",
        method="post",
        target="/api/ai/v1/jobs?b=2&a=1",
        body_sha256="a" * 64,
    ).decode() == (
        "BARAQ-HMAC-V2\nbaraq-django\ndjango-current\n1700000000\n"
        "nonce-for-test-0001\nPOST\n/api/ai/v1/jobs?a=1&b=2\n" + "a" * 64
    )


def test_shared_hmac_vector_has_expected_signature() -> None:
    headers = make_service_signature(
        method="POST",
        target="/api/ai/v1/jobs?b=2&a=1",
        body=b'{"a":1}',
        timestamp=1_700_000_000,
        nonce="nonce-for-test-0001",
        service="baraq-django",
        key_id="django-current",
    )
    assert (
        headers["X-Content-SHA256"]
        == "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    )
    assert (
        headers["X-Baraq-Signature"]
        == "5e41ddc5b38525cac1fe8c1e44d2d5fbbcae5a0a2788dba6e9021c76eb276f99"
    )


@pytest.mark.parametrize(
    ("mutator", "expected_code"),
    [
        (lambda headers: headers.__setitem__("X-Baraq-Key-Id", "unknown"), "unknown_key_id"),
        (
            lambda headers: headers.__setitem__("X-Baraq-Service", "other-service"),
            "unauthorized_service",
        ),
        (lambda headers: headers.__setitem__("X-Baraq-Signature", "0" * 63), "invalid_signature"),
        (lambda headers: headers.__setitem__("X-Content-SHA256", "0" * 64), "invalid_content_hash"),
    ],
)
def test_hmac_v2_rejects_invalid_headers(
    mutator: Callable[[dict[str, str]], None], expected_code: str
) -> None:
    headers = signed_headers()
    mutator(headers)
    with pytest.raises(AuthenticationError) as error:
        verify_service_signature(
            method="POST",
            target="/api/ai/v1/jobs?a=1",
            body=b'{"a":1}',
            headers=headers,
            now=1_700_000_000,
        )
    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("method", "target", "body"),
    [
        ("PATCH", "/api/ai/v1/jobs?a=1", b'{"a":1}'),
        ("POST", "/api/ai/v1/jobs?a=2", b'{"a":1}'),
        ("POST", "/api/ai/v1/jobs?a=1", b'{"a":2}'),
    ],
)
def test_hmac_v2_covers_method_target_and_body(method: str, target: str, body: bytes) -> None:
    with pytest.raises(AuthenticationError) as error:
        verify_service_signature(
            method=method,
            target=target,
            body=body,
            headers=signed_headers(),
            now=1_700_000_000,
        )
    assert error.value.code in {"invalid_signature", "invalid_content_hash"}


def test_hmac_v2_rejects_expired_and_future_timestamps() -> None:
    headers = signed_headers()
    with pytest.raises(AuthenticationError) as expired:
        verify_service_signature(
            method="POST",
            target="/api/ai/v1/jobs?a=1",
            body=b'{"a":1}',
            headers=headers,
            now=1_700_000_301,
        )
    assert expired.value.code == "expired_signature"
    with pytest.raises(AuthenticationError) as future:
        verify_service_signature(
            method="POST",
            target="/api/ai/v1/jobs?a=1",
            body=b'{"a":1}',
            headers=headers,
            now=1_699_999_699,
        )
    assert future.value.code == "expired_signature"


class FakeRedis:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        assert value == "1"
        assert nx is True
        assert ex == 600
        if key in self.keys:
            return False
        self.keys.add(key)
        return True


@pytest.mark.asyncio
async def test_hmac_v2_rejects_a_reused_nonce() -> None:
    redis = FakeRedis()
    headers = make_service_signature(
        method="POST",
        target="/api/ai/v1/jobs?a=1",
        body=b'{"a":1}',
        timestamp=int(time.time()),
        nonce="nonce-for-test-0001",
        service="baraq-django",
        key_id="django-current",
    )
    await verify_and_consume_service_signature(
        method="POST", target="/api/ai/v1/jobs?a=1", body=b'{"a":1}', headers=headers, redis=redis
    )
    with pytest.raises(AuthenticationError) as replay:
        await verify_and_consume_service_signature(
            method="POST",
            target="/api/ai/v1/jobs?a=1",
            body=b'{"a":1}',
            headers=headers,
            redis=redis,
        )
    assert replay.value.code == "replay_detected"
