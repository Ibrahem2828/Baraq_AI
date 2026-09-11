# Django ↔ Baraq AI contract (V2)

Status: `CURRENT` for Phase 1. The generated [OpenAPI](openapi.json) is the source of truth for HTTP request and response schemas; this document specifies the cross-service protocol and ownership rules.

## Job submission

`POST /api/ai/v1/jobs` is internal-only. Django signs it with Baraq Internal HMAC V2 and sends exactly this shape:

```json
{
  "contract_version": "2.0",
  "client_job_id": "3fca75f3-90d0-4e33-9c13-642644ec8e48",
  "user_id": "user-123",
  "task_type": "fahes_generate_quiz",
  "input": {"source_ids": ["source-123"]},
  "model_policy": {"tier": "balanced", "allow_fallback": true},
  "trace": {"request_id": "32b8196d-0ec1-41c0-8f4f-b27dfa434f20"}
}
```

Only these top-level fields are accepted. `client_job_id` and `trace.request_id` are UUIDs. Task-specific data must be inside `input`; each task has a strict schema and invalid input returns `422` with code `invalid_task_input` before a job is stored or a worker is dispatched.

Canonical task types are:

- `fahes_generate_quiz`
- `kholasa_generate_summary`
- `khota_generate_plan`
- `rasheed_recommendations`
- `sada_transcribe_audio`

The response is `202` with the durable job identity and initial `queued` status. A V1 adapter still accepts the previous unversioned body temporarily; it is deprecated and must not be used by new Django code. V2 does not depend on `Idempotency-Key`: `user_id + client_job_id` is the idempotency boundary.

## Idempotency

The AI service hashes the semantic payload (`task_type`, validated `input`, and `model_policy`) and stores it with the job.

- Same `user_id`, `client_job_id`, and semantic payload: returns the original job; no new outbox row is created.
- Same `user_id` and `client_job_id` with a different semantic payload: `409`, `idempotency_conflict`.
- Different users may use the same client UUID; a partial unique database index enforces this boundary under concurrent requests.

## Job lifecycle and dispatch

The job and one `process_ai_job` outbox event are written in one database transaction. A dispatcher claims persisted events, sends Celery messages, retries broker failures with bounded exponential backoff, and recovers stale claims. Delivery is at-least-once; workers atomically claim only `queued` jobs.

Persisted states are `queued`, `preparing`, `retrieving`, `planning`, `generating`, `validating`, `repairing`, `materializing`, `completed`, `failed`, and `canceled`. Terminal states never return to processing. Cancellation is idempotent and a worker checks it before output persistence.

## Source ownership boundary

Django is the authoritative owner of sources. Before any download, AI calls the following Django internal endpoints using HMAC V2 and the signed `user_id` query parameter:

```text
GET /api/internal/v1/ai/sources/{source_id}/manifest/?user_id={user_id}
GET /api/internal/v1/ai/sources/{source_id}/download/?user_id={user_id}
```

The manifest must contain `source_id`, `owner_user_id`, `title`, `mime_type`, `size_bytes`, and `content_sha256`. It may additionally expose a short-lived `download_url` and expiry. AI rejects a manifest whose `owner_user_id` differs from the job user with `403 source_forbidden`, before download, embeddings, or a provider call. Downloads are streamed, byte-bounded by the manifest, and checksum-verified.

`SadaPipeline` uses the same `get_source_manifest(source_id, user_id)` and `download_source(manifest, user_id)` interface as document ingestion; it does not trust a client filesystem path or filename for authorization.

## AI → Django requests

AI calls Django only through `BackendClient`. All calls use the same HMAC V2 protocol documented in [INTERNAL_AUTH_V2.md](INTERNAL_AUTH_V2.md), with `X-Baraq-Service: baraq-ai-service`. Django must accept that service, validate the key ID, timestamp, nonce, body hash, method, path, and canonical query string, and enforce the same replay cache rule.

No direct Django database access, credit handling, or materialization call is permitted from AI in this phase. Django may poll `GET /api/ai/v1/jobs/{client_job_id}` using HMAC V2.

## Stable error codes

Integration code must use `error.code`, not English error text. Relevant codes are `invalid_contract`, `invalid_task_input`, `invalid_signature`, `expired_signature`, `replay_detected`, `unknown_key_id`, `idempotency_conflict`, `source_forbidden`, `source_not_found`, `job_not_found`, and `invalid_job_transition`.

## Rollout checklist for Django

1. Implement the V2 signer using the shared vector in `INTERNAL_AUTH_V2.md`.
2. Submit V2 jobs in staging and poll using V2-signed requests.
3. Implement source manifest/download ownership checks with the exact query contract above.
4. Run the cross-repository V2 vector and golden integration test before disabling the V1 adapter.
