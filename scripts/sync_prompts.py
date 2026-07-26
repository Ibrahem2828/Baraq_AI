from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from app.db.session import AsyncSessionLocal
from app.models import load_all_models
from app.models.prompt import PromptVersion
from app.prompts.registry import get_prompt_registry
from app.schemas.fahes import FahesResult
from app.schemas.kholasa import KholasaResult
from app.schemas.khota import KhotaResult
from app.schemas.rasheed import RasheedResult
from app.schemas.sada import SadaResult

SCHEMAS = {
    "fahes_generate_quiz": FahesResult,
    "khota_generate_plan": KhotaResult,
    "rasheed_recommendations": RasheedResult,
    "kholasa_generate_summary": KholasaResult,
    "sada_transcribe_audio": SadaResult,
}


async def run() -> None:
    load_all_models()
    async with AsyncSessionLocal() as session:
        for spec in get_prompt_registry().list():
            schema_model = SCHEMAS[spec.task_type]
            existing = await session.scalar(
                select(PromptVersion).where(
                    PromptVersion.name == spec.name,
                    PromptVersion.version == spec.version,
                )
            )
            if existing and existing.checksum != spec.checksum:
                raise RuntimeError(
                    f"Prompt {spec.name} {spec.version} changed without a version bump"
                )
            await session.execute(
                update(PromptVersion)
                .where(PromptVersion.name == spec.name)
                .values(is_active=False)
            )
            if existing is None:
                existing = PromptVersion(
                    name=spec.name,
                    version=spec.version,
                    task_type=spec.task_type,
                    system_prompt=spec.system_prompt,
                    user_template=spec.user_template,
                    output_schema=schema_model.model_json_schema(),
                    metadata_json=spec.metadata,
                    checksum=spec.checksum,
                    is_active=True,
                    created_by="deployment",
                )
                session.add(existing)
            else:
                existing.is_active = True
        await session.commit()
    print("Prompt registry synchronized successfully")


if __name__ == "__main__":
    asyncio.run(run())
