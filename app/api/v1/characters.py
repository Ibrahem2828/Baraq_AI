"""Legacy direct-client character endpoints were retired in Phase 1.

The mobile app must call Django; Django calls POST /api/ai/v1/jobs.
"""

from fastapi import APIRouter

router = APIRouter()
