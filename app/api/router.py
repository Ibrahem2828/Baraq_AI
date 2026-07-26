from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin, characters, feedback, health, jobs

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(jobs.router)
api_router.include_router(feedback.router)
api_router.include_router(characters.router)
api_router.include_router(admin.router)
