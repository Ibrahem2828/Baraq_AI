# Baraq Internal HMAC V2

Status: `CURRENT`. This is the one protocol for Django → AI and AI → Django calls.

Required headers:

```text
X-Baraq-Service
X-Baraq-Key-Id
X-Baraq-Timestamp
X-Baraq-Nonce
X-Content-SHA256
X-Baraq-Signature
```

`X-Baraq-Timestamp` is Unix UTC seconds. `X-Baraq-Nonce` is a high-entropy value between 16 and 128 characters. `X-Content-SHA256` and `X-Baraq-Signature` are lowercase hexadecimal SHA-256 values.

## Canonicalization

The canonical byte string is UTF-8, newline-delimited, without a trailing newline:

```text
BARAQ-HMAC-V2
{service}
{key_id}
{timestamp}
{nonce}
{HTTP_METHOD_UPPERCASE}
{canonical_request_target}
{body_sha256}
```

`canonical_request_target` is the relative path plus canonical query string:

- The path must start with `/`; its trailing slash is preserved.
- Query parameters are parsed once, including blank values and repeated names.
- Pairs are sorted by decoded key and then decoded value.
- They are re-encoded using RFC 3986 encoding (`-._~` unescaped).
- An absent or empty query has no `?` suffix.

The signature is `HMAC-SHA256(secret_for_key_id, canonical_bytes).hexdigest()`.

## Shared test vector

This vector contains an intentionally non-production test secret. It must produce the same result in Django and AI implementations.

```text
secret: test-hmac-secret
service: baraq-django
key_id: django-current
timestamp: 1700000000
nonce: nonce-for-test-0001
method: POST
target input: /api/ai/v1/jobs?b=2&a=1
canonical target: /api/ai/v1/jobs?a=1&b=2
body UTF-8: {"a":1}
body_sha256: 015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862
signature: 5e41ddc5b38525cac1fe8c1e44d2d5fbbcae5a0a2788dba6e9021c76eb276f99
```

## Verification order

1. Require every header and validate the calling service and known key ID.
2. Compute the raw-body SHA-256 and compare it using `hmac.compare_digest`.
3. Reject timestamps outside `HMAC_MAX_CLOCK_SKEW_SECONDS` (default 300).
4. Rebuild the canonical bytes and compare the signature with `hmac.compare_digest`.
5. Atomically write `baraq:hmac:nonce:{service}:{nonce}` to Redis with `NX` and `HMAC_NONCE_TTL_SECONDS` (default 600).
6. Reject an existing nonce as `401 replay_detected` and do not execute the request.

Both sides must fail closed. Do not log a secret, raw signature, Authorization header, or full request body.

## Key rotation

`BARAQ_HMAC_KEYS_JSON` is a key-id to secret JSON object; `BARAQ_HMAC_CURRENT_KEY_ID` selects the signing key. Verification accepts every configured key, allowing a current and previous key during rotation. Remove the old key only after both services have switched. Production refuses placeholder or missing keys at startup. HMAC V1 is disabled by default; `HMAC_V1_COMPAT_ENABLED` exists only for an explicitly managed temporary migration.
