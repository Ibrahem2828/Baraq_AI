from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import AdminUser
from app.core.config import get_settings
from app.prompts.registry import get_prompt_registry
from app.schemas.common import APIEnvelope
from app.services.routing_config import get_routing_config

router = APIRouter(prefix="/admin", tags=["AI Administration"])


@router.get("/configuration", response_model=APIEnvelope[dict])
async def configuration(_: AdminUser) -> APIEnvelope[dict]:
    settings = get_settings()
    prompts = get_prompt_registry().list()
    routing = get_routing_config()
    return APIEnvelope(
        data={
            "service": {"name": settings.app_name, "version": settings.app_version},
            "providers": {
                "primary_enabled": settings.openai_primary_enabled,
                "secondary_enabled": settings.openai_secondary_enabled,
                "routing_policy": settings.provider_routing_policy,
            },
            "models": {
                "high_quality": settings.openai_model_high_quality,
                "balanced": settings.openai_model_balanced,
                "fast": settings.openai_model_fast,
                "embeddings": settings.openai_embedding_model,
                "transcription": settings.openai_transcription_model,
            },
            "prompts": [
                {
                    "name": item.name,
                    "version": item.version,
                    "task_type": item.task_type,
                    "checksum": item.checksum,
                }
                for item in prompts
            ],
            "routing_version": routing.version,
        }
    )
