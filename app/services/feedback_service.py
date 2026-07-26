from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import NotFoundError
from app.models.ai_job import AIJob, AIOutput
from app.models.feedback import AIFeedback
from app.schemas.feedback import FeedbackCreate
from app.training.candidate_builder import build_candidate


class FeedbackService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_or_update(
        self, *, user_id: str, request: FeedbackCreate
    ) -> tuple[AIFeedback, bool]:
        output = await self.session.scalar(
            select(AIOutput)
            .join(AIJob, AIOutput.job_id == AIJob.id)
            .where(AIOutput.id == request.output_id, AIJob.user_id == user_id)
            .options(selectinload(AIOutput.job))
        )
        if not output:
            raise NotFoundError("AI output not found")
        feedback = await self.session.scalar(
            select(AIFeedback).where(
                AIFeedback.output_id == output.id,
                AIFeedback.user_id == user_id,
            )
        )
        values = request.model_dump(mode="json", exclude={"output_id"})
        values["issue_types"] = [item.value for item in request.issue_types]
        if feedback:
            for key, value in values.items():
                setattr(feedback, key, value)
        else:
            feedback = AIFeedback(output_id=output.id, user_id=user_id, **values)
            self.session.add(feedback)
        await self.session.flush()
        candidate = build_candidate(job=output.job, output=output, feedback=feedback)
        created = False
        if candidate:
            self.session.add(candidate)
            created = True
        await self.session.commit()
        await self.session.refresh(feedback)
        return feedback, created
