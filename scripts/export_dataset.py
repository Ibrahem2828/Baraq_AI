from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db.session import AsyncSessionLocal
from app.models import load_all_models
from app.training.exporter import export_dataset_jsonl


async def run(dataset_name: str, task_type: str, output: Path) -> None:
    load_all_models()
    async with AsyncSessionLocal() as session:
        dataset = await export_dataset_jsonl(
            session=session,
            dataset_name=dataset_name,
            task_type=task_type,
            output_path=output,
        )
    print(
        f"dataset={dataset.name} items={dataset.item_count} "
        f"sha256={dataset.manifest_sha256} output={output}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze approved, anonymized Baraq AI candidates into JSONL."
    )
    parser.add_argument("--name", required=True, help="Immutable dataset version name")
    parser.add_argument("--task-type", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.name, args.task_type, args.output))


if __name__ == "__main__":
    main()
