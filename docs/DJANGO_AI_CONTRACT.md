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

No direct Django database access, credit handling, or materialization call is permitted from AI in this phase. Django may poll `GET /api/ai/v1/jobs/{client_job_id}?user_id={user_id}` and `POST /api/ai/v1/jobs/{client_job_id}/cancel?user_id={user_id}` using HMAC V2. `user_id` is required and must be the job owner: `backend_request_id` is unique only per `(user_id, backend_request_id)` (two tenants may legitimately reuse the same `client_job_id`), so both endpoints scope the lookup by `user_id` and return `404 job_not_found` for any other tenant's job, even one with a colliding id. `user_id` is part of the signed canonical query string, so Django cannot omit or spoof it without invalidating the HMAC signature.

## Stable error codes

Integration code must use `error.code`, not English error text. Relevant codes are `invalid_contract`, `invalid_task_input`, `invalid_signature`, `expired_signature`, `replay_detected`, `unknown_key_id`, `idempotency_conflict`, `source_forbidden`, `source_not_found`, `job_not_found`, and `invalid_job_transition`.

## Rollout checklist for Django

1. Implement the V2 signer using the shared vector in `INTERNAL_AUTH_V2.md`.
2. Submit V2 jobs in staging and poll using V2-signed requests.
3. Implement source manifest/download ownership checks with the exact query contract above.
4. Run the cross-repository V2 vector and golden integration test before disabling the V1 adapter.

## Rollout order for the required `user_id` on GET/cancel (2026-09-12)

`GET /api/ai/v1/jobs/{client_job_id}` and `POST /api/ai/v1/jobs/{client_job_id}/cancel` now
require `user_id` as a query parameter (previously the lookup was not tenant-scoped at all — a
cross-tenant IDOR). This is a breaking contract change and **must be sequenced, not deployed
atomically**, because HMAC V2 signs whatever query string is actually sent
(`canonical_request_target` in `app/core/security.py` parses and sorts it into the signed
canonical request) — there is no signing-scheme migration to coordinate, only the query string
Django chooses to send.

**Verified rollout order (do this, not a simultaneous deploy):**

1. **Django ships `user_id` on both endpoints first**, signs it as part of the request (the
   existing V2 signer already covers query parameters, so no signer change is needed — only the
   query string Django builds). Verify in staging against the *current* (pre-enforcement)
   Baraq_AI: confirmed by test that the current code silently ignores the extra query parameter
   and proceeds unaffected (it isn't declared on the old endpoint signature, and FastAPI does not
   reject undeclared query parameters) — so this step is safe to ship ahead of Baraq_AI with zero
   behavior change on either side.
2. **Deploy this Baraq_AI change** once step 1 is confirmed live in staging/production. Verified by
   test that the reverse order breaks cleanly and loudly, not silently or insecurely: a legacy
   Django request signed *without* `user_id` gets a `422 invalid_contract`
   (`{"loc": ["query", "user_id"], "type": "missing"}`) from FastAPI's own parameter validation —
   never a wrong-tenant result, a 5xx, or a signature bypass. So if the order is ever accidentally
   reversed, the failure mode is safe (loud rejection), just not available — it is still not safe
   to *rely on* that order, since it means an outage for every GET/cancel call until Django
   catches up.
3. Confirm `docs/openapi.json` (regenerated with `BARAQ_RUNTIME_MODE=service`) reflects `user_id`
   as `required` on both operations before treating this as closed.

**Do not** treat this as safe to deploy atomically/simultaneously "close enough" — the two
verified failure modes above are asymmetric (harmless no-op vs. total outage of two endpoints),
so there is a clear correct order, not just a preference.
