# Graders

- `deterministic/` -> implemented in `app/evals/graders.py` (imported by
  `scripts/run_evals.py`), not duplicated here. Schema/duplicate/citation/
  policy checks that don't need a judge model.
- `model_judge/` -> not implemented yet. Needs a live Gemini/OpenAI judge
  call (spec section 25: "LLM-as-judge only for dimensions that can't be
  measured deterministically") -- blocked on A2 provider activation.
- `human_review/` -> not implemented yet. Needs a review UI/sheet and a
  reviewer pool; the Lab's 1-5 rating + flags (`/lab/<character>`, spec
  section 31) is the raw feedback input this stage would consume.
