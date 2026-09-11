# Operational runbooks (spec section 36)

Each entry names the actual mechanism it uses -- no step here assumes a
process nobody has coded.

## Rotate a Gemini/OpenAI key

1. Add the new key to the secret manager / CI protected variable under a
   distinct name (`OPENAI_PRIMARY_API_KEY`, `GEMINI_PRIMARY_API_KEY`, ...).
   Never edit a committed file.
2. Deploy with the new value. `ProviderRouter._build_instances()`
   (`app/providers/router.py`) reads it fresh on process start.
3. Confirm health via the Lab's `/lab/overview` + `/lab/health` (non-production
   only) or a scoped live probe, then revoke the old key at the provider.
4. No restart-time downtime is required beyond a normal rolling deploy --
   there is no in-memory-only credential cache to invalidate separately.

## Rotate the Django <-> AI HMAC key

`BARAQ_HMAC_KEYS_JSON` (`app/core/config.py`) is a keyring, not a single
value: keep the current and previous key IDs both present during the
rotation window, flip `BARAQ_HMAC_CURRENT_KEY_ID` on the signer side once
Django is confirmed to accept the new key, then drop the previous entry
after the deploy has fully rolled out. Never remove the currently-signing
key from the keyring first.

## Provider 429 spike / outage

- Check `/lab/evals-providers` (non-production) or the `AI_PROVIDER_ATTEMPTS`
  / circuit-breaker metrics for the affected `ProviderAccount`.
- The circuit breaker (`app/providers/circuit_breaker.py`) opens
  automatically after `PROVIDER_CIRCUIT_FAILURE_THRESHOLD` failures and
  cools down for `PROVIDER_COOLDOWN_SECONDS` -- no manual action is usually
  needed; Gemini and OpenAI circuits are independent, so one outage never
  blocks the other provider's candidates.
- To force a provider out of rotation immediately, lower its priority (or
  remove its candidate entry) in `config/model_routing.yaml` and redeploy --
  this is a config change, not a pipeline change (spec section 5).

## Stop a model/provider from routing

Edit `config/model_routing.yaml`: remove or reorder the candidate for the
affected task type. `ProviderRouter.ordered_candidates()` reads it on every
router construction; no code change or migration is required.

## Resend an outbox event / drain the DLQ

`app/services/outbox_dispatcher.py` and `app/services/webhook_delivery.py`
own the durable dispatch/delivery lifecycle (`DispatchOutboxStatus`,
bounded exponential backoff). Operationally: query
`JobDispatchOutboxEvent`/webhook delivery rows by status, and re-trigger the
dispatcher task for a stuck `pending` row rather than mutating the row by
hand -- this preserves the idempotency guarantee (`event_id` is stable).

## Reindex embeddings after a model/version change

Any `embedding_model`/`embedding_dimensions`/chunking policy change must
bump the corresponding version field and reindex -- never search across
mixed embedding versions as one set (spec section 11). Today the
version-metadata columns on `SourceChunk` are a known gap (see the
production acceptance report); adding them is a prerequisite for a real
reindex runbook, tracked there rather than promised here.

## Purge a user/project's data

`raw_content_retention_days` / `job_retention_days` (`app/core/config.py`)
drive scheduled retention cleanup. An explicit user-initiated deletion
request should call the same cleanup path immediately rather than waiting
for the retention window -- wire this into the Django-triggered purge
workflow (spec section 24) before this is a complete runbook.

## Rollback a prompt/model/routing version

- Prompts: `app/prompts/registry.py` versions by file + checksum;
  `scripts/sync_prompts.py` refuses a checksum change without a version
  bump. Roll back by redeploying the previous prompt YAML version and
  re-running the sync script.
- Routing: `config/model_routing.yaml` is plain config -- revert the file
  and redeploy.

## Restore PostgreSQL and verify indexes/outbox

Restore from the durable backup, then run `alembic upgrade head` against
the restored database and verify: `pgvector` extension present, all
`ai_*` indexes exist, and no `JobDispatchOutboxEvent` row is stuck in
`dispatching` past `OUTBOX_LOCK_TIMEOUT_SECONDS` (stale-lock recovery
should reclaim it automatically; verify manually after a restore since the
lock clock resets with the restore point).
