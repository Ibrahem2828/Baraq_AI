# Model routing, providers and budgets

## PROVIDER_MODE

One switch controls the whole provider layer: `PROVIDER_MODE=mock|replay|live`.

- `mock` (default) -- `MockProvider` returns fixed, schema-valid payloads
  per task type (`app/providers/mock_provider.py`). Zero network, zero cost.
  Proves code/contract safety, never model quality (spec section 6).
- `replay` -- `ReplayProvider` loads sanitized JSON fixtures from
  `tests/fixtures/replay/<task_type>/<scenario>.json` (falling back to
  `tests/fixtures/replay/_common/<scenario>.json` for shared negative
  scenarios: `provider_timeout`, `provider_rate_limited`,
  `provider_unavailable`, `auth_failed`, `empty_output`, `invalid_json`).
- `live` -- real Gemini and/or OpenAI calls. Required in production
  (`Settings.validate_production` rejects any other value).

Both Mock and Replay implement the exact same `LLMProvider` interface as
Gemini/OpenAI and pass through the real `StructuredGenerationService` and
domain/grounding validators -- there is no bypass of the production path.

## ProviderCandidate

`config/model_routing.yaml` declares, per task, a list of candidates:

```yaml
fahes_generate_quiz:
  candidates:
    - {provider: gemini, quality_tier: high_quality, priority: 10}
    - {provider: openai, quality_tier: high_quality, priority: 20}
```

`ProviderRouter.ordered_candidates()` resolves each entry into a
`ProviderCandidate` that carries its own `provider`, `account_id`, `model`
(via `app/providers/model_aliases.py`, never hard-coded), `quality_tier`,
`timeout_seconds`, `max_output_tokens`, `budget_policy` and
`pricing_version`. A candidate's model is never swapped to a different
provider mid-fallback (spec section 5) -- Gemini and OpenAI failing over to
each other always means "try the *next candidate*", each with its own model.

Starting policy: Gemini first (economical default for long/repetitive
tasks), OpenAI second (higher quality/sensitivity fallback). This is pure
config -- flip `priority` after a live A2 eval without touching pipeline
code (spec section 26).

`model_policy.allow_fallback=false` (persisted on `AIJob`) limits the router
to its top-priority candidate only; retries do not change provider or model.

## Capability matrix

`app/providers/capabilities.py` declares which provider supports which
capability (`structured_generation`, `embeddings`, `transcription`,
`moderation`). Today: OpenAI supports all four; Gemini supports structured
generation and embeddings only. The router filters candidates by capability
before selection, so a task can never be routed to a provider that can't
serve it. Embeddings and transcription (not one of the five canonical task
types) are routed by `ProviderRouter.candidates_for_capability()`.

## Circuit breakers and budgets

Each `ProviderAccount` (`openai_primary`, `openai_secondary`,
`gemini_primary`) has its own Redis-backed circuit breaker
(`app/providers/circuit_breaker.py`) and monthly budget
(`app/services/provider_budget.py`). A Gemini outage never opens OpenAI's
breaker or drains its budget, and vice versa.

## Pricing

`config/pricing.yaml` carries `pricing_version` and one entry per model with
`status: pending_verification` until a real, dated price list is confirmed.
`scripts/validate_ai_release.py`'s `pricing_is_ready_for_live_mode()` only
enforces non-pending pricing once `PROVIDER_MODE=live` -- at A1, with no real
spend possible, a pending price is a documented gap, not a release blocker
(spec section 22 governs *active* models).

## Adding a provider

Adding a vendor beyond Gemini/OpenAI/Mock/Replay requires: an `LLMProvider`
adapter, a capability-matrix entry, model aliases in `Settings` +
`model_aliases.py`, pricing entries, and an eval-backed routing decision --
never a pipeline rewrite and never a name hard-coded inside a pipeline.
