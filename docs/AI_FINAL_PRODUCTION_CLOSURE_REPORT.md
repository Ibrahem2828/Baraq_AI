# AI SECTION RESULT: FAIL

Date: 2026-08-20

This is a fail-closed production closure report. It records commands executed
in this workspace; `PASS` is not inferred from code review or unit tests.

## 1. Baseline

The audited service is FastAPI + SQLAlchemy/Alembic + PostgreSQL/pgvector +
Redis + Celery. It exposes health, HMAC-protected Django job and feedback
routes. The five task types are Fahes, Kholasa, Khota, Rasheed and Sada. HMAC
V2 with nonce replay protection, a job state machine and a dispatch outbox were
present before final closure work. The worktree was already dirty; unrelated
existing changes were preserved.

## 2. Critical issues found and fixed

- Fixed invalid `ai-beat.depends_on` Compose YAML and added a no-env-resolution
  Compose validation gate. Production no longer publishes the AI API port;
  `docker-compose.dev.yml` supplies the local port.
- Persisted `model_policy.allow_fallback` and enforced it in router, generation
  retry and Sada transcription routing.
- Persisted `trace.request_id` in `AIJob`, dispatch events, attempts and output;
  it now drives FastAPI response identity and structured job/outbox/webhook
  logs.
- Added migration `0004_final_runtime_delivery` for additive trace, source
  version, prompt/pipeline and fallback fields.
- Added durable `deliver_result_webhook` events. Completion, output and webhook
  event commit together; failed callbacks use bounded exponential backoff
  without re-running generation.
- Added frozen source hash scope, evidence-rich citations and deterministic
  claim/evidence and transcript-preservation gates.

## 3. Runtime core

No fresh disposable PostgreSQL, Redis, worker, beat or Django materialisation
runtime was successfully started in this environment. The runtime gate is
therefore failed.

| Component | Result |
| --- | --- |
| FastAPI contract/unit exercise | PASS |
| PostgreSQL fresh migration | FAIL |
| pgvector | FAIL |
| Redis | FAIL |
| Celery Worker | FAIL |
| Celery Beat | FAIL |
| Job Outbox runtime | FAIL |
| Result Webhook Outbox runtime | FAIL |
| Django HMAC interoperability | PASS |
| Source ownership unit coverage | PASS |
| Docker Compose Config | PASS |
| Docker Build | FAIL |
| AI Lab acceptance | FAIL |
| Live Generation Provider | FAIL |
| Live Embeddings | FAIL |
| Live Transcription | FAIL |

## 4. Contract and HMAC

V2 remains the primary Django-to-AI contract. The request has UUID
`client_job_id`, strict task input, `model_policy` and `trace.request_id`.
`scripts/validate_django_hmac_interop.py` passed against the local Django
integration implementation. HMAC V2 is also used for the AI-to-Django result
webhook. Tampering and replay have contract/security test coverage.

## 5. Jobs, outbox and webhooks

The durable lifecycle is:

```text
AIJob + process_ai_job event (one transaction)
  -> Celery worker -> AIOutput + completed + deliver_result_webhook (one transaction)
  -> signed Django webhook -> Django idempotency by event_id
```

`process_ai_job` and `deliver_result_webhook` have explicit Celery handlers.
Kombu transport failures are returned to `pending` immediately with capped
exponential backoff; no broad exception swallow marks delivery successful.
This design is code/test validated, but the mandatory broker-down recovery and
duplicate-delivery runtime test were not executed.

## 6. Database and migrations

`0004_final_runtime_delivery` is additive and forward-safe for historical
metadata-created initial schemas. It backfills legacy trace values from job UUID
only during migration; new requests preserve the supplied trace UUID.

`python -m alembic upgrade head` was attempted against the local default
configuration and failed because PostgreSQL rejected the configured placeholder
password. Fresh schema, constraints, pgvector extension and indexes are not
runtime-verified.

## 7. Redis and Celery

Port 6379 was unreachable. Consequently nonce TTL/replay persistence,
circuit-breaker storage, worker/beat dispatch and recovery cannot be claimed.
Queue routes and worker subscriptions now match: `ai_interactive`, `ai_audio`,
`ai_ingestion`, `ai_background`.

## 8. Fahes

Fahes now declares `SELECTED_SOURCES_ONLY`, freezes source versions, rejects
insufficient context, carries evidence identifiers and validates question,
answer and explanation against cited evidence. Its full deterministic and live
character E2E gates remain unexecuted.

## 9. Kholasa

Kholasa now declares `SELECTED_SOURCES_ONLY`, uses the frozen hash scope and
derives groundedness from summary/flashcard evidence support rather than a
constant. Its full deterministic and live character E2E gates remain
unexecuted.

## 10. Khota

Khota declares `AUTHORITATIVE_CONSTRAINTS`; it validates date bounds, excluded
dates, duplicate days and daily time limits, with optional frozen source scope.
Its deterministic scheduling and E2E acceptance gate remains unexecuted.

## 11. Rasheed

Rasheed declares `AUTHORITATIVE_DATA_ONLY` and rejects a request with no
authoritative metrics. Its quality score is derived from supplied authoritative
data coverage. Its deterministic and live E2E acceptance gates remain
unexecuted.

## 12. Sada

Sada freezes the audio hash at acceptance, audits transcription provider
attempts and measures cleanup preservation from transcript tokens. Its live
audio/transcription test remains unexecuted.

## 13. RAG and grounding

Retrieval filters by user, source IDs and exact captured hashes. Citations now
carry evidence/chunk identity, hash, page/section and semantic/lexical/rerank
scores. The deterministic validator blocks missing, invalid or lexically
unsupported references. This is a tested source-grounding control, not a claim
of universal hallucination elimination.

## 14. Providers and models

The provider boundary remains `LLMProvider`; the deployed adapter is currently
OpenAI Responses-compatible with primary/secondary accounts, circuit breaking,
monthly budget accounting and explicit fallback policy.

## 15. Cost and budget

`pricing.yaml` still contains placeholder zero values, so real cost enforcement
and model promotion cannot be accepted until approved live pricing and
evaluation evidence are provided.

## 16. AI Lab

`ENABLE_AI_LAB=true` mounts a non-production-only Lab landing page; production
never mounts it. It was not opened against a running stack and is not accepted
as an operator Lab with all five executable flows.

## 17. Evals

Evaluation policy and release thresholds are documented in
`docs/EVALUATION.md`; a golden dataset, semantic judge policy and deterministic
character E2E suite are still blockers.

## 18. Security

HMAC V2, replay protection, HMAC key rotation, source ownership, bounded
downloads, no-store responses and structured trace fields are implemented. A
real secret-history scan remains a release check, not verified evidence here.

## 19. Privacy and retention

Retention cleanup exists for terminal jobs. Retention execution against a real
database was not verified, and remains a release blocker.

## 20. Observability

Trace fields are persisted and logged without full document text. Runtime
metrics collection, queue/outbox age metrics and operational alerting were not
verified in a running deployment.

## 21. Docker and deployment

`docker compose config --quiet --no-env-resolution --no-interpolate` passed.
The Dockerfile no longer trusts every forwarded IP. `docker build -t
baraq-ai-final-current .` failed with `buildx .lock: Access is denied`; Docker
Desktop service `com.docker.service` was stopped.

## 22. Tests

```text
All pytest: 55 passed
Unit: 14 collected/passed
Contract: 17 collected/passed
Security: 12 collected/passed
RAG directory: 0
Fahes directory: 0
Kholasa directory: 0
Khota directory: 0
Rasheed directory: 0
Sada directory: 0
Integration directory: 0
E2E directory: 0
Adversarial directory: 0
```

`ruff` and `mypy` both passed. The missing category suites are a release
blocker, not equivalent to skipped successful tests.

## 23. Live Provider Results

No real configured provider credential or reachable Docker/Redis stack was
available. Result: **FAIL**.

## 24. Full E2E

No source-based Django -> AI -> provider -> webhook -> Django materialisation
journey was executed. Result: **FAIL**.

## 25. Remaining blockers

1. Start Docker Desktop or provide an equivalent isolated stack with Postgres
   pgvector, Redis, Celery worker and beat.
2. Supply disposable PostgreSQL credentials via
   `AI_RELEASE_TEST_DATABASE_SYNC_URL`; run migration/schema assertions.
3. Configure real non-placeholder HMAC/database/provider secrets outside Git.
4. Run broker-down outbox recovery, duplicate task/webhook and cancellation
   materialisation tests against Django.
5. Run all five deterministic character E2Es plus a bounded live-provider
   source-based smoke test; approve non-placeholder pricing and eval thresholds.
6. Exercise the protected Lab against the running stack and add the missing
   integration/E2E/adversarial suites.

## 26. Backend handoff

See [BACKEND_AI_HANDOFF.md](BACKEND_AI_HANDOFF.md). Backend review is the next
scope; no general Backend refactor was performed for this report.

## 27. Exact commands executed

```text
python -m ruff check .
python -m mypy .
python -m pytest
python scripts/export_openapi.py
python scripts/validate_package.py
python scripts/validate_django_hmac_interop.py
python -u scripts/validate_ai_release.py
docker compose config --quiet --no-env-resolution --no-interpolate
docker build -t baraq-ai-final-current .
python -m alembic upgrade head
Test-NetConnection localhost:5432
Test-NetConnection localhost:6379
```

The release validator output was `AI SECTION RESULT: FAIL` because fresh
PostgreSQL migration, Docker build, Redis/Celery durable E2E and live-provider
smoke were not successfully run.
