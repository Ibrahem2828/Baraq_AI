# Phase 1 production core report

> **SUPERSEDED — 2026-08-19.** The final Phase 1 result and current runtime evidence are in [PHASE_1_CLOSURE_REPORT.md](PHASE_1_CLOSURE_REPORT.md). This file is retained as the pre-Django-V2 checkpoint.

## 1. Executive Result

```text
PHASE 1: FAIL
```

The AI-side core has been implemented and unit/contract/security tests pass, but Phase 1 cannot be closed. The adjacent Django repository still implements the previous signing and payload contract, and Docker/Redis/Celery/fresh-PostgreSQL runtime gates were not verified.

## 2. Baseline Before Changes

Baseline recorded before edits on 2026-08-19:

| Command | Result | Evidence |
| --- | --- | --- |
| `python --version; python -m compileall app` | PASS | Python `3.12.2`; compile completed. |
| `python -m pytest` | FAIL | collection stopped at `ModuleNotFoundError: docx`. |
| `ruff check .` | NOT RUN | executable was not installed on PATH. |
| `python -m mypy .` | NOT RUN | module was not installed. |

Initial code findings: unversioned job submission, HMAC based only on timestamp/method/path/body hash, no key ID or replay protection, idempotency ignored payload differences, direct `process_ai_job.delay()` after database commit, no central state machine, and Sada called a `BackendClient` interface that did not exist. The initial AI migration `0001_initial` uses `Base.metadata.create_all`, so it is not an explicit historical schema snapshot.

## 3. Architecture Changes

- Added strict Contract V2 plus a deprecated V1 adapter.
- Added Baraq Internal HMAC V2 with canonical target/query handling, key IDs, clock bounds, constant-time comparison, and Redis nonce replay protection.
- Made `client_job_id` semantic idempotency boundary; payload differences now return `409 idempotency_conflict`.
- Added the central job state machine, safe cancellation, and duplicate-worker claim protection.
- Added transactional AI-job dispatch outbox, bounded retries, and stale-lock recovery.
- Unified source-manifest/download methods with `user_id`; Sada now uses the same verified flow.
- Persisted pipeline warnings, security flags, and validation report on `AIOutput`.

## 4. Files Changed

- `app/schemas/jobs.py`, `app/api/v1/jobs.py`: Contract V2 and API boundary validation.
- `app/core/security.py`, `app/api/dependencies.py`, `app/core/config.py`: HMAC V2 and fail-closed configuration.
- `app/services/job_service.py`, `app/services/job_state_machine.py`, `app/services/job_processor.py`: idempotency, state policy, worker safety.
- `app/models/ai_job.py`, `app/services/outbox_dispatcher.py`, `app/workers/*`, `alembic/versions/0003_phase1_production_core.py`: durable dispatch and schema additions.
- `app/services/backend_client.py`, `app/services/source_ingestion.py`, `app/pipelines/sada.py`: source ownership and corrected Sada boundary.
- `app/core/security_flags.py`, `app/pipelines/*`: persisted suspicious-source signals.
- `.env.example`, `docker-compose.yml`, `pyproject.toml`: keyring settings, safe local password placeholder, explicit `psycopg`, migration container.
- `docs/DJANGO_AI_CONTRACT.md`, `docs/INTERNAL_AUTH_V2.md`, `docs/openapi.json`, `scripts/validate_phase1.py`, and `tests/**`: specification, generated API, validation entrypoint, and coverage.

## 5. Contract V2

Primary endpoint: `POST /api/ai/v1/jobs`.

Required fields are `contract_version: "2.0"`, UUID `client_job_id`, `user_id`, canonical `task_type`, strict task `input`, `model_policy`, and `trace.request_id`. Unknown top-level fields return `422 invalid_contract`; a task input missing required source/metric/date/audio data returns `422 invalid_task_input`. The supported V1 adapter is explicitly deprecated and is not the primary OpenAPI contract.

## 6. HMAC V2

AI implementation is complete and tested. Required headers are service, key ID, timestamp, nonce, content SHA-256, and signature. Canonical bytes include method, path plus sorted/re-encoded query, and body hash. Keys are selected from `BARAQ_HMAC_KEYS_JSON`, with a current ID and optional previous IDs. Redis uses `baraq:hmac:nonce:{service}:{nonce}` with a bounded TTL.

The shared vector is documented in `INTERNAL_AUTH_V2.md` and tested in `tests/security/test_hmac_v2.py`. Django currently uses a different `timestamp + "." + body` signer and `X-Baraq-Internal-Key`; therefore cross-repository V2 verification is **not complete**.

## 7. Database Changes

`0003_phase1_production_core` adds security-output columns, new active status values, the dispatch outbox, unique dispatch constraint, partial unique `(user_id, backend_request_id)` index, and pending-event index. `psycopg[binary]` is now explicit.

`python -m alembic heads` reports `0003_phase1_production_core (head)`. A fresh PostgreSQL upgrade was **not run**: port 5432 is reachable locally, but the database is not proven disposable and no migration is run against an unknown database. The pre-existing dynamic initial migration remains a migration-history issue; it was not rewritten because it may already be applied.

## 8. Test Results

| Command | Result |
| --- | --- |
| `python -m pytest` | PASS — `50 passed in 4.35s` |
| `python scripts/export_openapi.py` | PASS — regenerated `docs/openapi.json` |
| `python scripts/validate_package.py` | PASS — 111 Python files, no detected hard-coded OpenAI key pattern |
| `python -m alembic heads` | PASS — `0003_phase1_production_core (head)` |
| `python scripts/validate_phase1.py` | FAIL — tests/package/OpenAPI pass; lint, typecheck, and migration gate fail/not run |
| `python -m ruff check .` | FAIL — 32 pre-existing/project-wide lint findings remain after scoped formatting |
| `python -m mypy .` | FAIL — 14 project-wide typing findings remain |

Security test coverage includes valid/tampered body, method, path, query, malformed signature, wrong key/service, time window, replay nonce, and the documented vector. HTTP contract tests use FastAPI `TestClient`; a tampered signed request returns `401 invalid_content_hash` before database access.

## 9. Runtime Results

| Component | Result | Evidence |
| --- | --- | --- |
| FastAPI liveness | PASS | TestClient `GET /api/ai/v1/health/live` returned `200`, `ok`. |
| PostgreSQL migration | NOT RUN | local port 5432 exists but database ownership/emptiness is unknown. |
| Redis | FAIL / not available | local port 6379 connection test returned false. |
| Celery | NOT RUN | requires a working Redis broker. |
| Docker build | NOT RUN | `docker build -t baraq-ai-phase1-validation .` could not connect to `dockerDesktopLinuxEngine`. |

## 10. Security Results

- HMAC V2 code and tests: PASS on AI side.
- Replay protection: PASS with a deterministic Redis `SET NX EX` test.
- Source ownership: PASS on AI boundary test; a mismatched Django manifest is rejected as `source_forbidden` before download.
- Injection warning/security-flag persistence: IMPLEMENTED; database end-to-end persistence needs PostgreSQL runtime verification.
- Secret handling: no secret values were printed. `.env.example` contains only local placeholders. `ROTATION REQUIRED`: any deployed legacy Django internal key/webhook secret must be replaced by the shared V2 keyring during the coordinated rollout.

## 11. Compatibility Notes

Unversioned V1 job input remains a deprecated adapter so queued/rolling Django callers are not broken immediately. It normalizes to the internal V2 model but lacks V2 wire-level guarantees. V1 must be disabled only after Django sends and verifies V2 in staging.

## 12. Remaining Blockers

1. Django code is present at `B:\baraaq\Baraaq_back\backend`, but it currently uses legacy signing, old task names, arbitrary top-level payload fields, and internal-key source endpoints. The workspace grants writes only to `Baraq_AI`; an attempted security-file modification outside that workspace was rejected. Django V2 implementation and cross-repository tests require explicit write authorization.
2. Fresh, disposable PostgreSQL migration test has not run.
3. Redis, Celery, and Docker runtime gates have not run because Redis/Docker daemon are unavailable.
4. Project-wide Ruff and mypy gates still fail on the documented findings.
5. The historic dynamic `0001_initial` migration should be replaced only through a separately approved migration-history strategy if it has been deployed.

## 13. Phase 2 Handoff

Phase 2 can rely on the AI-side V2 contract, canonical HMAC vectors, strict task schema registry, semantic idempotency, state transition policy, durable dispatch outbox, source ownership interface, and persisted security signals. Before any Phase 2 product work, finish the Django V2 rollout and rerun the blocked runtime gates.
