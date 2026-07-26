from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencies import CurrentUser, DbSession
from app.schemas.common import APIEnvelope
from app.schemas.feedback import FeedbackCreate, FeedbackView
from app.services.feedback_service import FeedbackService

router = APIRouter(prefix="/feedback", tags=["AI Feedback"])


@router.post("", response_model=APIEnvelope[FeedbackView], status_code=status.HTTP_201_CREATED)
async def create_feedback(
    payload: FeedbackCreate,
    session: DbSession,
    user: CurrentUser,
) -> APIEnvelope[FeedbackView]:
    feedback, candidate_created = await FeedbackService(session).create_or_update(
        user_id=user.user_id,
        request=payload,
    )
    return APIEnvelope(
        data=FeedbackView(
            feedback_id=feedback.id,
            candidate_created=candidate_created,
        )
    )
