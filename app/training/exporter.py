from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CandidateStatus
from app.models.feedback import DatasetVersion, TrainingDatasetCandidate


async def export_dataset_jsonl(
    *,
    session: AsyncSession,
    dataset_name: str,
    task_type: str,
    output_path: Path,
) -> DatasetVersion:
    candidates = list(
        (
            await session.scalars(
                select(TrainingDatasetCandidate).where(
                    TrainingDatasetCandidate.task_type == task_type,
                    TrainingDatasetCandidate.status == CandidateStatus.APPROVED,
                )
            )
        ).all()
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with output_path.open("w", encoding="utf-8") as handle:
        for item in candidates:
            row = {
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(item.input_json, ensure_ascii=False),
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(item.expected_output_json, ensure_ascii=False),
                    },
                ],
                "metadata": {
                    "candidate_id": str(item.id),
                    "task_type": item.task_type,
                },
            }
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
            item.status = CandidateStatus.EXPORTED
    dataset = DatasetVersion(
        name=dataset_name,
        task_type=task_type,
        manifest_sha256=digest.hexdigest(),
        item_count=len(candidates),
        status="frozen",
        metadata_json={"path": str(output_path)},
    )
    session.add(dataset)
    await session.commit()
    return dataset
