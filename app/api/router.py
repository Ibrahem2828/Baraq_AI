from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import feedback, health, jobs

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(jobs.router)
api_router.include_router(feedback.router)

# Direct client and AI-admin routes are not mounted in production. Django is the gateway.
