# Baraq AI handoff for the Django team

## Base URLs and health

AI is internal behind Django/reverse proxy. Its service prefix is
`/api/ai/v1`; the primary routes are:

```text
POST /api/ai/v1/jobs
GET  /api/ai/v1/jobs/{client_job_id}
POST /api/ai/v1/jobs/{client_job_id}/cancel
POST /api/ai/v1/feedback
GET  /api/ai/v1/health/live
GET  /api/ai/v1/health/ready
```

`/health/live` means process alive. `/health/ready` requires DB, Redis and
Alembic head and returns 503 when unavailable.

## V2 job request

`POST /jobs` accepts only service-signed JSON:

```json
{
  "contract_version": "2.0",
  "client_job_id": "00000000-0000-0000-0000-000000000010",
  "user_id": "user-123",
  "task_type": "kholasa_generate_summary",
  "input": {"source_ids": ["source-123"]},
  "model_policy": {"tier": "balanced", "allow_fallback": true},
  "trace": {"request_id": "00000000-0000-0000-0000-000000000011"}
}
```

Canonical task names are `fahes_generate_quiz`, `kholasa_generate_summary`,
`khota_generate_plan`, `rasheed_recommendations`, and
`sada_transcribe_audio`. Full input and output schemas are generated in
`docs/openapi.json`.

Idempotency is `(user_id, client_job_id)`: the same semantic request returns
the original job; a changed semantic request returns `409 idempotency_conflict`.
`allow_fallback=false` prohibits provider/model failover after the selected
provider fails.

## HMAC V2

Both directions use the headers:

```text
X-Baraq-Service
X-Baraq-Key-Id
X-Baraq-Timestamp
X-Baraq-Nonce
X-Content-SHA256
X-Baraq-Signature
```

The newline-delimited canonical request is documented in
`docs/INTERNAL_AUTH_V2.md`. Django must reject stale timestamps, unknown key
IDs, bad body hashes/signatures and replayed nonces. The service identifier for
Django -> AI is `baraq-django`; AI -> Django is `baraq-ai-service`.

## Source endpoints required by AI

These Django internal endpoints must enforce ownership before returning any
source bytes:

```text
GET /api/internal/v1/ai/sources/{source_id}/manifest/?user_id={user_id}
GET /api/internal/v1/ai/sources/{source_id}/download/?user_id={user_id}
GET /api/internal/v1/ai/collections/{collection_id}/manifest/
GET /api/internal/v1/ai/users/{user_id}/context/
```

The source manifest must include `source_id`, `owner_user_id`, `title`,
`mime_type`, `size_bytes`, `content_sha256` and optional metadata. AI freezes
that hash at job creation and rejects a changed source before provider use.

## Job status and result webhook

Job states include `queued`, `preparing`, `retrieving`, `planning`,
`generating`, `validating`, `repairing`, `materializing`, `completed`,
`failed`, `canceled`. Django should treat the result webhook as the durable
completion signal and must idempotently store `event_id`.

AI sends signed `POST /api/internal/v1/ai/webhooks/jobs/`:

```json
{
  "event_id": "stable-event-uuid",
  "event_type": "ai.job.completed",
  "job_id": "the-original-client_job_id",
  "status": "completed",
  "result": {},
  "metadata": {
    "request_id": "trace-uuid",
    "verification": {},
    "quality": {"quality_score": 0.0, "groundedness_score": 0.0},
    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    "provider": {"account": "openai_primary", "model": "..."},
    "warnings": [],
    "security_flags": []
  }
}
```

Return any 2xx only after durable idempotent materialisation/status update.
AI retries non-2xx/network failures from its result outbox with bounded
exponential backoff. Duplicate events must not duplicate materialised records.

## Stable integration errors and timeouts

Use `error.code`, not display text. Important codes: `invalid_contract`,
`invalid_task_input`, `idempotency_conflict`, `source_forbidden`,
`source_version_changed`, `insufficient_source_context`, `missing_evidence`,
`unsupported_claim`, `replay_detected`, `invalid_signature` and
`backend_unreachable`. Default internal HTTP timeout is 30 seconds; callback
timeout setting is 15 seconds.
