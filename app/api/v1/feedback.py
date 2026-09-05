from __future__ import annotations

from fastapi import APIRouter, status

from app.api.dependencies import DbSession, DjangoService
from app.schemas.common import APIEnvelope
from app.schemas.feedback import DjangoFeedbackCreate, FeedbackCreate, FeedbackView
from app.services.feedback_service import FeedbackService

router = APIRouter(prefix="/feedback", tags=["Django Gateway Feedback"])


@router.post("", response_model=APIEnvelope[FeedbackView], status_code=status.HTTP_201_CREATED)
async def create_feedback(
    payload: DjangoFeedbackCreate,
    session: DbSession,
    _: DjangoService,
) -> APIEnvelope[FeedbackView]:
    feedback_request = FeedbackCreate.model_validate(
        payload.model_dump(mode="json", exclude={"user_id"})
    )
    feedback, candidate_created = await FeedbackService(session).create_or_update(
        user_id=payload.user_id,
        request=feedback_request,
    )
    return APIEnvelope(
        data=FeedbackView(
            feedback_id=feedback.id,
            candidate_created=candidate_created,
        )
    )
