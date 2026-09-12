from __future__ import annotations

import json
import time

from app.core.config import get_settings
from app.core.errors import ProviderError, ValidationFailure
from app.core.security_flags import suspicious_source_flags
from app.models.ai_job import ProviderAttempt
from app.models.enums import ProviderAttemptStatus
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.providers.base import ProviderResult, TranscriptionResult
from app.providers.capabilities import Capability
from app.providers.model_aliases import transcription_model_for
from app.rag.grounding import transcript_preservation_score
from app.rag.guard import sanitize_untrusted_source
from app.schemas.sada import SadaCleanupResult, SadaRequest, SadaResult, TranscriptSegment
from app.services.cost import get_cost_calculator
from app.services.knowledge_policy import KnowledgePolicy
from app.services.routing_config import get_routing_config
from app.utils.hash import sha256_bytes


def _transcription_as_provider_result(transcription: TranscriptionResult) -> ProviderResult:
    """Wrap a transcription call's usage/cost as a ProviderResult -- used
    both to reconcile the transcription budget reservation and, for
    cleanup_level="literal" (no cleanup LLM call at all), as the
    PipelineResult.provider_result the job output/dashboards attribute cost
    to, so a literal-mode job's real transcription cost is never silently
    missing from AIOutput."""
    return ProviderResult(
        data={"transcription": True},
        account=transcription.account,
        model=transcription.model,
        response_id=transcription.response_id,
        usage=transcription.usage,
        latency_ms=transcription.latency_ms,
        estimated_cost_usd=transcription.estimated_cost_usd,
    )


def _normalize_literal_transcript(text: str) -> str:
    """Whitespace-only normalization for cleanup_level="literal": collapses
    runs of spaces/tabs/newlines, changes no words. No LLM call is made for
    this level (spec: STT -> normalized transcript -> return)."""
    return " ".join(text.split())


class SadaPipeline(AIPipeline):
    knowledge_policy = KnowledgePolicy.TRANSCRIPT_PRESERVATION
    version = "2"

    async def execute(self, context: PipelineContext) -> PipelineResult:
        settings = get_settings()
        request = SadaRequest.model_validate(context.job.request_payload)
        manifest = await context.backend.get_source_manifest(
            source_id=request.source_id,
            user_id=context.job.user_id,
            project_id=context.job.project_id,
        )
        expected_hash = context.job.source_versions.get(request.source_id)
        if expected_hash != manifest.content_sha256:
            raise ValidationFailure(
                "Audio source changed after the job was accepted",
                code="source_version_changed",
            )
        if not manifest.mime_type.startswith("audio/"):
            raise ValidationFailure("Sada requires an audio source", code="audio_source_required")
        if manifest.size_bytes > settings.max_audio_file_bytes:
            raise ValidationFailure(
                "Audio file exceeds the configured size limit", code="audio_too_large"
            )
        content = await context.backend.download_source(
            manifest=manifest, user_id=context.job.user_id
        )
        if sha256_bytes(content) != manifest.content_sha256:
            raise ValidationFailure("Audio checksum mismatch", code="source_checksum_mismatch")

        candidates = await context.generation.router.candidates_for_capability(
            Capability.TRANSCRIPTION,
            f"{context.job.id}:sada:transcription",
            allow_fallback=context.job.allow_fallback,
        )
        transcription = None
        last_error: Exception | None = None
        for number, candidate in enumerate(candidates, start=1):
            if candidate.instance is None:
                continue
            model = transcription_model_for(settings, candidate.provider)
            # Duration is only known once the provider returns; the audio
            # length ceiling this job enforces (settings.max_audio_seconds)
            # is the only pre-call bound available, so it's used as the
            # reservation ceiling for every transcription model regardless
            # of whether it's actually duration- or token-priced.
            max_call_cost = get_cost_calculator().max_transcription_cost(
                model, max_seconds=settings.max_audio_seconds
            )
            reservation = await context.generation.budget.reserve(
                candidate.account_id, max_call_cost
            )
            if not reservation.granted:
                continue
            attempt = ProviderAttempt(
                job_id=context.job.id,
                attempt_number=len(context.job.attempts) + number,
                provider_account=candidate.account_id,
                model_name=model,
                request_id=context.job.request_id,
                status=ProviderAttemptStatus.STARTED,
            )
            context.session.add(attempt)
            await context.session.flush()
            started = time.perf_counter()
            try:
                transcription = await candidate.instance.transcribe(
                    model=model,
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
                await context.generation.router.circuit.record_success(candidate.account_id)
                await context.generation.budget.commit_actual(
                    reservation, _transcription_as_provider_result(transcription)
                )
                await context.session.commit()
                break
            except Exception as exc:
                last_error = exc
                attempt.status = ProviderAttemptStatus.FAILED
                attempt.error_code = exc.__class__.__name__
                attempt.error_message = str(exc)[:4000]
                attempt.retryable = True
                attempt.latency_ms = int((time.perf_counter() - started) * 1000)
                await context.generation.budget.release(reservation)
                await context.generation.router.circuit.record_failure(candidate.account_id)
                await context.session.commit()
                if not context.job.allow_fallback:
                    break
        if transcription is None:
            raise ProviderError(
                "All transcription provider accounts failed",
                code="transcription_failed",
                retryable=True,
            ) from last_error
        if not transcription.text.strip():
            raise ValidationFailure("The audio transcription is empty", code="empty_transcription")

        # The uploaded audio is exactly as untrusted as any other source
        # (spec: transcript content is user-supplied, not the model's own
        # instructions) -- run it through the same injection guard fahes/
        # kholasa/khota apply to every retrieved chunk before it reaches a
        # prompt, instead of trusting it implicitly.
        guarded_transcript = sanitize_untrusted_source(transcription.text)

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

        if request.cleanup_level == "literal":
            # No LLM call: STT -> deterministic normalization -> return.
            cleaned_transcript = _normalize_literal_transcript(transcription.text)
            detected_topics: list[str] = []
            important_terms: list[str] = []
            cleanup_warnings: list[str] = []
            provider_result = _transcription_as_provider_result(transcription)
        else:
            routing = get_routing_config().get(context.job.task_type.value)
            prompt = get_prompt_registry().get(routing.prompt or "sada_cleanup_transcript")
            context.job.prompt_name = prompt.name
            context.job.prompt_version = prompt.version
            context.job.prompt_checksum = prompt.checksum
            # Segment *timing* (start/end/speaker) is genuinely new
            # information the model needs to localize warnings -- segment
            # *text* is not sent again here, since it's the same words
            # already in raw_transcript below (was previously duplicated,
            # roughly doubling input tokens for the transcript's content).
            segment_timeline = [
                {"start": seg.start_seconds, "end": seg.end_seconds, "speaker": seg.speaker}
                for seg in normalized_segments
            ]
            user_input = prompt.render_user(
                task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
                raw_transcript=guarded_transcript.safe_text,
                segment_timeline=json.dumps(segment_timeline, ensure_ascii=False),
            )
            cleanup = await context.generation.generate(
                job=context.job,
                routing=routing,
                prompt=prompt,
                user_input=user_input,
                output_model=SadaCleanupResult,
            )
            cleanup_result = SadaCleanupResult.model_validate(cleanup.data)
            cleaned_transcript = cleanup_result.cleaned_transcript
            detected_topics = cleanup_result.detected_topics
            important_terms = cleanup_result.important_terms
            cleanup_warnings = cleanup_result.warnings
            provider_result = cleanup

        result = SadaResult(
            full_transcript=transcription.text,
            cleaned_transcript=cleaned_transcript,
            segments=normalized_segments,
            detected_topics=detected_topics,
            important_terms=important_terms,
            duration_seconds=transcription.usage.audio_seconds or None,
            language=request.language,
            warnings=cleanup_warnings,
        )
        preservation = transcript_preservation_score(
            raw_transcript=transcription.text,
            cleaned_transcript=result.cleaned_transcript,
        )
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=[],
            provider_result=provider_result,
            quality_score=preservation,
            groundedness_score=preservation,
            warnings=["suspicious_source_content"] if guarded_transcript.suspicious else [],
            security_flags=suspicious_source_flags([request.source_id])
            if guarded_transcript.suspicious
            else [],
        )
