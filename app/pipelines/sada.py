from __future__ import annotations

import json
import time

from app.core.config import get_settings
from app.core.errors import ProviderError, ValidationFailure
from app.models.ai_job import ProviderAttempt
from app.models.enums import ProviderAttemptStatus
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.providers.base import ProviderResult
from app.schemas.sada import SadaRequest, SadaResult, TranscriptSegment
from app.services.routing_config import get_routing_config
from app.utils.hash import sha256_bytes


class SadaPipeline(AIPipeline):
    async def execute(self, context: PipelineContext) -> PipelineResult:
        settings = get_settings()
        request = SadaRequest.model_validate(context.job.request_payload)
        manifest = await context.backend.get_source_manifest(
            source_id=request.source_id, user_id=context.job.user_id
        )
        if not manifest.mime_type.startswith("audio/"):
            raise ValidationFailure("Sada requires an audio source", code="audio_source_required")
        if manifest.size_bytes > settings.max_audio_file_bytes:
            raise ValidationFailure("Audio file exceeds the configured size limit", code="audio_too_large")
        content = await context.backend.download_source(manifest)
        if sha256_bytes(content) != manifest.content_sha256:
            raise ValidationFailure("Audio checksum mismatch", code="source_checksum_mismatch")

        candidates = await context.generation.router.ordered_candidates(
            f"{context.job.id}:sada:transcription"
        )
        transcription = None
        last_error: Exception | None = None
        for number, candidate in enumerate(candidates, start=1):
            if not await context.generation.budget.has_budget(candidate.account):
                continue
            attempt = ProviderAttempt(
                job_id=context.job.id,
                attempt_number=len(context.job.attempts) + number,
                provider_account=candidate.account,
                model_name=settings.openai_transcription_model,
                status=ProviderAttemptStatus.STARTED,
            )
            context.session.add(attempt)
            await context.session.flush()
            started = time.perf_counter()
            try:
                transcription = await candidate.provider.transcribe(
                    model=settings.openai_transcription_model,
                    filename=manifest.title,
                    content=content,
                    language=request.language,
                    prompt="مصطلحات تعليمية متوقعة: " + "، ".join(request.known_terms[:100]),
                    diarize=request.diarize,
                )
                attempt.status = ProviderAttemptStatus.SUCCEEDED
                attempt.provider_response_id = transcription.response_id
                attempt.latency_ms = transcription.latency_ms
                attempt.input_tokens = transcription.usage.input_tokens
                attempt.output_tokens = transcription.usage.output_tokens
                attempt.estimated_cost_usd = transcription.estimated_cost_usd
                await context.generation.router.circuit.record_success(candidate.account)
                await context.generation.budget.record(
                    ProviderResult(
                        data={"transcription": True},
                        account=transcription.account,
                        model=transcription.model,
                        response_id=transcription.response_id,
                        usage=transcription.usage,
                        latency_ms=transcription.latency_ms,
                        estimated_cost_usd=transcription.estimated_cost_usd,
                    )
                )
                await context.session.commit()
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                attempt.status = ProviderAttemptStatus.FAILED
                attempt.error_code = exc.__class__.__name__
                attempt.error_message = str(exc)[:4000]
                attempt.retryable = True
                attempt.latency_ms = int((time.perf_counter() - started) * 1000)
                await context.generation.router.circuit.record_failure(candidate.account)
                await context.session.commit()
        if transcription is None:
            raise ProviderError(
                "All transcription provider accounts failed",
                code="transcription_failed",
                retryable=True,
            ) from last_error
        if not transcription.text.strip():
            raise ValidationFailure("The audio transcription is empty", code="empty_transcription")

        routing = get_routing_config().get(context.job.task_type.value)
        prompt = get_prompt_registry().get(routing.prompt or "sada_cleanup_transcript")
        context.job.prompt_name = prompt.name
        context.job.prompt_version = prompt.version
        user_input = prompt.render_user(
            task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
            raw_transcript=transcription.text,
            segments=json.dumps(transcription.segments, ensure_ascii=False),
        )
        cleanup = await context.generation.generate(
            job=context.job,
            routing=routing,
            prompt=prompt,
            user_input=user_input,
            output_model=SadaResult,
        )
        result = SadaResult.model_validate(cleanup.data)
        normalized_segments: list[TranscriptSegment] = []
        for segment in transcription.segments:
            start = float(segment.get("start", segment.get("start_seconds", 0)) or 0)
            end = float(segment.get("end", segment.get("end_seconds", start)) or start)
            text = str(segment.get("text") or "").strip()
            if text:
                normalized_segments.append(
                    TranscriptSegment(
                        start_seconds=start,
                        end_seconds=max(start, end),
                        text=text,
                        speaker=segment.get("speaker"),
                        confidence=segment.get("confidence"),
                    )
                )
        result = result.model_copy(
            update={
                "full_transcript": transcription.text,
                "segments": normalized_segments or result.segments,
                "language": request.language,
            }
        )
        # The cleanup generation is the final provider metadata. The transcription attempt remains audited.
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=[],
            provider_result=cleanup,
            quality_score=0.9,
            groundedness_score=1.0,
        )
