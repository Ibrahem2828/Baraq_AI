"""Seed eval runner (spec section 25).

Drives each dataset case through the exact same provider-neutral,
schema-validated, grounding-checked path the Lab uses
(``BaraqAIApplication``) -- not a shortcut. Runs under whatever
``PROVIDER_MODE`` is configured; with mock/replay this proves the harness and
the grading logic are wired correctly, not model quality (spec section 6/31).
A live run (real Gemini/OpenAI keys) is what turns these into a genuine
quality signal for the A2 routing decision (spec section 26).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from app.application.standalone import BaraqAIApplication
from app.core.config import Settings
from app.core.errors import AppError
from app.evals.graders import GRADERS, GradeResult
from app.lab.providers import build_lab_provider
from app.lab.storage import LabStorage
from app.models.enums import TaskType


async def run_case(case: dict[str, Any], *, provider_mode: str) -> dict[str, Any]:
    settings = Settings(
        lab_storage_dir=Path(tempfile.mkdtemp(prefix="baraq-eval-")),
        provider_mode=provider_mode,
    )
    storage = LabStorage(settings)
    workspace = storage.ensure_workspace()
    source_ids: list[str] = []
    for source in case.get("sources", []):
        stored = storage.store_upload(
            workspace_id=workspace.workspace_id,
            filename=source["filename"],
            mime_type="text/plain",
            content=str(source["content"]).encode("utf-8"),
        )
        source_ids.append(stored.source_id)

    input_payload = dict(case["input"])
    if source_ids and "source_ids" not in input_payload:
        input_payload["source_ids"] = source_ids

    provider = build_lab_provider(settings)
    application = BaraqAIApplication(settings=settings, storage=storage, provider=provider)
    task_type = TaskType(case["task_type"])
    try:
        output = await application.run_task(
            workspace_id=workspace.workspace_id, task_type=task_type, input=input_payload
        )
    except AppError as exc:
        return {
            "id": case["id"],
            "task_type": case["task_type"],
            "passed": False,
            "metrics": {},
            "failures": [f"pipeline_error:{exc.code}"],
        }
    except Exception as exc:  # a malformed case must not crash the whole batch
        return {
            "id": case["id"],
            "task_type": case["task_type"],
            "passed": False,
            "metrics": {},
            "failures": [f"unexpected_error:{exc.__class__.__name__}"],
        }

    grader = GRADERS.get(case["task_type"])
    grade = grader(output.result, output.validation) if grader else GradeResult(passed=True)
    return {
        "id": case["id"],
        "task_type": case["task_type"],
        "passed": grade.passed,
        "metrics": grade.metrics,
        "failures": grade.failures,
        "provider": output.provider,
    }
