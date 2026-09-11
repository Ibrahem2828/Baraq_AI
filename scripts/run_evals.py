"""Run the seed eval datasets and write a report (spec section 25/28).

Usage:
    python scripts/run_evals.py [--provider-mode mock|replay|live]

Exits non-zero if any case fails, so CI can gate on it. This is the
"regression eval on every prompt/model/routing change" hook from spec
section 25 -- point it at the same dataset before and after a change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.evals.runner import run_case

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = REPO_ROOT / "evals" / "datasets"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"


def _load_cases(character: str) -> list[dict[str, Any]]:
    path = DATASETS_DIR / character / "seed.jsonl"
    if not path.exists():
        return []
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


async def main(provider_mode: str) -> int:
    characters = ["fahes", "kholasa", "khota", "rasheed"]
    all_results: list[dict[str, Any]] = []
    for character in characters:
        for case in _load_cases(character):
            result = await run_case(case, provider_mode=provider_mode)
            all_results.append(result)
            status = "PASS" if result["passed"] else "FAIL"
            print(f"[{status}] {result['id']} ({result['task_type']}) {result['failures']}")

    passed = sum(1 for r in all_results if r["passed"])
    total = len(all_results)
    summary = {
        "provider_mode": provider_mode,
        "generated_at": datetime.now(UTC).isoformat(),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "results": all_results,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"seed-eval-{provider_mode}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{passed}/{total} passed. Report: {report_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-mode", default="mock", choices=["mock", "replay", "live"])
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.provider_mode)))
