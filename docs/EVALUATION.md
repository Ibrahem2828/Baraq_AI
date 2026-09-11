# Evaluation policy

Spec section 25's rule: tests prove code safety, evals prove behavioral
quality. Deterministic graders run first; LLM-as-judge only for dimensions
that can't be measured deterministically; human review last.

## What exists today

```
evals/
  datasets/
    fahes/seed.jsonl        # 2 cases
    kholasa/seed.jsonl      # 2 cases
    khota/seed.jsonl        # 2 cases
    rasheed/seed.jsonl      # 2 cases
    sada/README.md          # needs a real audio fixture, see below
    rag/README.md           # placeholder -- needs real ingested content
    security/README.md      # placeholder -- prompt-injection content cases
  graders/README.md         # points at app/evals/graders.py + status of
                             # model_judge/human_review stages
  reports/                  # scripts/run_evals.py output lands here
app/evals/
  graders.py                # deterministic checks (duplicates, citation
                             # presence, groundedness floor, policy flags)
  runner.py                 # drives a case through BaraqAIApplication --
                             # the same schema-validated, grounding-checked
                             # path Lab uses, not a shortcut
scripts/run_evals.py        # CLI: --provider-mode mock|replay|live
```

Run it:

```
python scripts/run_evals.py --provider-mode mock
```

This is a **seed** dataset (2 cases per character, per the plan's honest
scope note) -- expand toward the spec's 50-100-per-character target before
A2, with real Arabic/English, long/short, messy-text, no-context and
conflicting-context cases (spec section 25).

## Reading a mock-mode report honestly

`evals/reports/seed-eval-mock.json` currently shows Fahes/Khota/Rasheed
passing and Kholasa failing `groundedness_below_floor`. That is expected and
intentional, not a bug to silently fix: `MockProvider` returns one fixed
canned answer per task type, and the seed source content was only aligned
closely enough with Fahes's fixture to prove the harness plumbing works
end-to-end. **A mock-mode pass is evidence the code path, schema validation
and grounding checks are wired correctly -- it is never evidence of model
quality** (spec sections 6 and 31 make this the same rule for the Lab UI's
"SIMULATED" badge). Real groundedness numbers only mean something once this
dataset runs under `--provider-mode live` against Gemini/OpenAI, or under
`replay` with fixtures deliberately matched to each case's source content.

## Sada

Sada needs a real audio file for Local Whisper, so it isn't part of the
generic JSON runner. Use:

```
python scripts/smoke_lab_characters.py --audio path/to/sample.wav
```

## Release gate integration

`scripts/validate_ai_release.py` runs the seed dataset as an **advisory,
non-blocking** check (`seed_evals`) -- visible in every release report, never
silently skipped, but not yet a hard gate until the dataset is large enough
and (for a real quality signal) run in `live` mode. Golden citation/
grounding/duplicate/schema thresholds from spec sections 15-18 are the
target once the dataset and a real provider are both available.

## Not yet implemented

- `model_judge` graders (need a live judge call -- blocked on A2).
- `human_review` pipeline (needs a review UI/reviewer pool; the Lab's 1-5
  rating + flags is the raw input this stage would consume).
- RAG and security (prompt-injection) datasets -- see the `README.md` in
  each `evals/datasets/` subdirectory for what's required before A2.
