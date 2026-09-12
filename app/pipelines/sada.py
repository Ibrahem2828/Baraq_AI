from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from pydub import AudioSegment
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.chunking import (
    AudioChunker,
    ChunkPlan,
    chunks_from_boundaries,
    plan_chunk_boundaries,
)
from app.audio.merge import merge_chunk_segments
from app.core.config import Settings, get_settings
from app.core.errors import ProviderError, ValidationFailure
from app.core.profanity import redact_model_list, redact_profanity
from app.core.security_flags import suspicious_source_flags
from app.db.session import AsyncSessionLocal
from app.models.ai_job import ProviderAttempt
from app.models.enums import ProviderAttemptStatus
from app.pipelines.base import AIPipeline, PipelineContext, PipelineResult
from app.prompts.registry import get_prompt_registry
from app.providers.base import ProviderResult, ProviderUsage, TranscriptionResult
from app.providers.candidate import ProviderCandidate
from app.providers.capabilities import Capability
from app.providers.circuit_breaker import ProviderCircuitBreaker
from app.providers.model_aliases import transcription_model_for
from app.rag.grounding import transcript_preservation_score
from app.rag.guard import sanitize_untrusted_source
from app.schemas.sada import SadaCleanupResult, SadaRequest, SadaResult, TranscriptSegment
from app.services.cost import get_cost_calculator
from app.services.knowledge_policy import KnowledgePolicy
from app.services.provider_budget import ProviderBudgetService
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


async def _transcribe_with_retry(
    *,
    session: AsyncSession,
    budget: ProviderBudgetService,
    circuit: ProviderCircuitBreaker,
    settings: Settings,
    candidates: list[ProviderCandidate],
    content: bytes,
    filename: str,
    language: str,
    prompt: str | None,
    diarize: bool,
    allow_fallback: bool,
    job_id: uuid.UUID,
    request_id: str,
    attempt_number_base: int,
) -> TranscriptionResult:
    """One provider-candidate fallback/retry loop for a single transcription
    call. Extracted so both the plain (unchunked) path and each independent
    chunk of a long recording (blueprint 02_AI_PLATFORM.md §8.4: "retry لكل
    chunk بشكل مستقل") share the exact same reservation/attempt/circuit
    bookkeeping instead of two divergent copies.

    `budget`/`circuit` are taken directly (not a whole
    StructuredGenerationService) because both are already safe to share
    across concurrent chunk calls -- ProviderBudgetService's
    reserve/commit_actual/release each open their own dedicated session
    internally (see app/services/provider_budget.py) regardless of which
    session constructed it, and the circuit breaker is Redis-backed. Only
    `session` (for this call's own ProviderAttempt bookkeeping) needs a
    fresh one per concurrent chunk -- one AsyncSession cannot safely be
    used from more than one concurrent coroutine. The unchunked path passes
    the pipeline's own long-lived session (unchanged behavior).
    """
    transcription: TranscriptionResult | None = None
    last_error: Exception | None = None
    for number, candidate in enumerate(candidates, start=1):
        if candidate.instance is None:
            continue
        model = transcription_model_for(settings, candidate.provider)
        # Duration is only known once the provider returns; the audio
        # length ceiling this job enforces (settings.max_audio_seconds) is
        # the only pre-call bound available, so it's used as the
        # reservation ceiling regardless of whether this is a whole-file or
        # single-chunk call.
        max_call_cost = get_cost_calculator().max_transcription_cost(
            model, max_seconds=settings.max_audio_seconds
        )
        reservation = await budget.reserve(candidate.account_id, max_call_cost)
        if not reservation.granted:
            continue
        attempt = ProviderAttempt(
            job_id=job_id,
            attempt_number=attempt_number_base + number,
            provider_account=candidate.account_id,
            model_name=model,
            request_id=request_id,
            status=ProviderAttemptStatus.STARTED,
        )
        session.add(attempt)
        await session.flush()
        started = time.perf_counter()
        try:
            transcription = await candidate.instance.transcribe(
                model=model,
                filename=filename,
                content=content,
                language=language,
                prompt=prompt,
                diarize=diarize,
            )
            attempt.status = ProviderAttemptStatus.SUCCEEDED
            attempt.provider_response_id = transcription.response_id
            attempt.latency_ms = transcription.latency_ms
            attempt.input_tokens = transcription.usage.input_tokens
            attempt.output_tokens = transcription.usage.output_tokens
            attempt.estimated_cost_usd = transcription.estimated_cost_usd
            await circuit.record_success(candidate.account_id)
            await budget.commit_actual(
                reservation, _transcription_as_provider_result(transcription)
            )
            await session.commit()
            break
        except Exception as exc:
            last_error = exc
            attempt.status = ProviderAttemptStatus.FAILED
            attempt.error_code = exc.__class__.__name__
            attempt.error_message = str(exc)[:4000]
            attempt.retryable = True
            attempt.latency_ms = int((time.perf_counter() - started) * 1000)
            await budget.release(reservation)
            await circuit.record_failure(candidate.account_id)
            await session.commit()
            if not allow_fallback:
                break
    if transcription is None:
        raise ProviderError(
            "All transcription provider accounts failed",
            code="transcription_failed",
            retryable=True,
        ) from last_error
    return transcription


def _normalize_segments(
    raw_segments: list[dict[str, Any]], *, time_offset_seconds: float = 0.0
) -> list[TranscriptSegment]:
    normalized: list[TranscriptSegment] = []
    for segment in raw_segments:
        local_start = float(segment.get("start", segment.get("start_seconds", 0)) or 0)
        local_end = float(
            segment.get("end", segment.get("end_seconds", local_start)) or local_start
        )
        start = local_start + time_offset_seconds
        end = local_end + time_offset_seconds
        text = str(segment.get("text") or "").strip()
        if text:
            normalized.append(
                TranscriptSegment(
                    start_seconds=start,
                    end_seconds=max(start, end),
                    text=text,
                    speaker=segment.get("speaker"),
                    confidence=segment.get("confidence"),
                )
            )
    return normalized


class SadaPipeline(AIPipeline):
    knowledge_policy = KnowledgePolicy.TRANSCRIPT_PRESERVATION
    version = "3"

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
        known_terms_prompt = "مصطلحات تعليمية متوقعة: " + "، ".join(request.known_terms[:100])
        existing_attempt_count = len(context.job.attempts)

        chunking = self._plan_chunks_if_long(
            content=content, manifest_mime_type=manifest.mime_type, settings=settings
        )

        if chunking is None:
            transcription = await _transcribe_with_retry(
                session=context.session,
                budget=context.generation.budget,
                circuit=context.generation.router.circuit,
                settings=settings,
                candidates=candidates,
                content=content,
                filename=manifest.title,
                language=request.language,
                prompt=known_terms_prompt,
                diarize=request.diarize,
                allow_fallback=context.job.allow_fallback,
                job_id=context.job.id,
                request_id=context.job.request_id,
                attempt_number_base=existing_attempt_count,
            )
            full_text = transcription.text
            normalized_segments = _normalize_segments(transcription.segments)
            representative = transcription
        else:
            audio, chunker, chunk_plans = chunking
            full_text, normalized_segments, representative = await self._transcribe_chunked(
                context=context,
                settings=settings,
                candidates=candidates,
                audio=audio,
                chunker=chunker,
                manifest_title=manifest.title,
                language=request.language,
                known_terms_prompt=known_terms_prompt,
                diarize=request.diarize,
                chunk_plans=chunk_plans,
                existing_attempt_count=existing_attempt_count,
            )

        if not full_text.strip():
            raise ValidationFailure("The audio transcription is empty", code="empty_transcription")

        # The uploaded audio is exactly as untrusted as any other source
        # (spec: transcript content is user-supplied, not the model's own
        # instructions) -- run it through the same injection guard fahes/
        # kholasa/khota apply to every retrieved chunk before it reaches a
        # prompt, instead of trusting it implicitly.
        guarded_transcript = sanitize_untrusted_source(full_text)

        if request.cleanup_level == "literal":
            # No LLM call: STT -> deterministic normalization -> return.
            cleaned_transcript = _normalize_literal_transcript(full_text)
            detected_topics: list[str] = []
            important_terms: list[str] = []
            cleanup_warnings: list[str] = []
            provider_result = _transcription_as_provider_result(representative)
        else:
            routing = get_routing_config().get(context.job.task_type.value)
            prompt_spec = get_prompt_registry().get(routing.prompt or "sada_cleanup_transcript")
            context.job.prompt_name = prompt_spec.name
            context.job.prompt_version = prompt_spec.version
            context.job.prompt_checksum = prompt_spec.checksum
            # Segment *timing* (start/end/speaker) is genuinely new
            # information the model needs to localize warnings -- segment
            # *text* is not sent again here, since it's the same words
            # already in raw_transcript below (was previously duplicated,
            # roughly doubling input tokens for the transcript's content).
            segment_timeline = [
                {"start": seg.start_seconds, "end": seg.end_seconds, "speaker": seg.speaker}
                for seg in normalized_segments
            ]
            user_input = prompt_spec.render_user(
                task_parameters=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
                raw_transcript=guarded_transcript.safe_text,
                segment_timeline=json.dumps(segment_timeline, ensure_ascii=False),
            )
            cleanup = await context.generation.generate(
                job=context.job,
                routing=routing,
                prompt=prompt_spec,
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
            full_transcript=full_text,
            cleaned_transcript=cleaned_transcript,
            segments=normalized_segments,
            detected_topics=detected_topics,
            important_terms=important_terms,
            duration_seconds=representative.usage.audio_seconds or None,
            language=request.language,
            warnings=cleanup_warnings,
        )
        preservation = transcript_preservation_score(
            raw_transcript=full_text,
            cleaned_transcript=result.cleaned_transcript,
        )
        # Blueprint 02_AI_PLATFORM.md §3.4: the *displayed* transcript may
        # show a redacted form like "[لفظ محجوب]", keeping the timestamp --
        # measured against full_transcript (the raw, internally-kept copy)
        # above, before this redaction, so the fidelity check isn't skewed
        # by our own redaction pass.
        redacted_cleaned, cleaned_hit = redact_profanity(result.cleaned_transcript)
        redacted_segments, segments_hit = redact_model_list(result.segments, text_fields=("text",))
        profanity_redacted = cleaned_hit or segments_hit
        if profanity_redacted:
            result = result.model_copy(
                update={"cleaned_transcript": redacted_cleaned, "segments": redacted_segments}
            )
        return PipelineResult(
            result_json=result.model_dump(mode="json"),
            citations=[],
            provider_result=provider_result,
            quality_score=preservation,
            groundedness_score=preservation,
            warnings=(["suspicious_source_content"] if guarded_transcript.suspicious else [])
            + (["profanity_redacted"] if profanity_redacted else []),
            security_flags=suspicious_source_flags([request.source_id])
            if guarded_transcript.suspicious
            else [],
        )

    @staticmethod
    def _plan_chunks_if_long(
        *, content: bytes, manifest_mime_type: str, settings: Settings
    ) -> tuple[AudioSegment, AudioChunker, list[ChunkPlan]] | None:
        """None means "use the existing single-call path" -- either the
        recording is short enough, or duration couldn't be probed (an
        unsupported/undecodable format) and falling back to one whole-file
        call is safer than failing the job outright."""
        chunker = AudioChunker(
            min_silence_len_ms=settings.sada_chunk_min_silence_len_ms,
            silence_thresh_dbfs=settings.sada_chunk_silence_thresh_dbfs,
        )
        try:
            audio = chunker.load(content, mime_type=manifest_mime_type)
        except Exception:
            return None
        duration_ms = len(audio)
        if duration_ms <= settings.sada_chunk_threshold_seconds * 1000:
            return None
        silences = chunker.detect_silences(audio)
        boundaries = plan_chunk_boundaries(
            duration_ms=duration_ms,
            target_ms=settings.sada_chunk_target_seconds * 1000,
            silences=silences,
            search_window_ms=settings.sada_chunk_silence_search_window_seconds * 1000,
        )
        chunk_plans = chunks_from_boundaries(
            boundaries=boundaries, overlap_ms=settings.sada_chunk_overlap_seconds * 1000
        )
        return audio, chunker, chunk_plans

    async def _transcribe_chunked(
        self,
        *,
        context: PipelineContext,
        settings: Settings,
        candidates: list[ProviderCandidate],
        audio: AudioSegment,
        chunker: AudioChunker,
        manifest_title: str,
        language: str,
        known_terms_prompt: str,
        diarize: bool,
        chunk_plans: list[ChunkPlan],
        existing_attempt_count: int,
    ) -> tuple[str, list[TranscriptSegment], TranscriptionResult]:
        """Blueprint 02_AI_PLATFORM.md §8.4: bounded parallelism, independent
        per-chunk retry, and a deterministic final order regardless of which
        chunk finishes first (asyncio.gather preserves input order)."""
        semaphore = asyncio.Semaphore(max(1, settings.sada_max_concurrent_chunks))
        progress_lock = asyncio.Lock()
        completed = 0
        total = len(chunk_plans)
        await self._update_progress(context, "تم تجهيز الصوت")

        async def _run_chunk(plan: ChunkPlan) -> TranscriptionResult:
            nonlocal completed
            chunk_bytes = chunker.export_chunk(audio, plan)
            async with semaphore, AsyncSessionLocal() as chunk_session:
                result = await _transcribe_with_retry(
                    session=chunk_session,
                    budget=context.generation.budget,
                    circuit=context.generation.router.circuit,
                    settings=settings,
                    candidates=candidates,
                    content=chunk_bytes,
                    filename=f"{manifest_title}.chunk{plan.index}.wav",
                    language=language,
                    prompt=known_terms_prompt,
                    diarize=diarize,
                    allow_fallback=context.job.allow_fallback,
                    job_id=context.job.id,
                    request_id=context.job.request_id,
                    # Chunk-scoped, collision-free without any shared
                    # counter: concurrent chunks never need to coordinate.
                    attempt_number_base=existing_attempt_count + plan.index * 100,
                )
            async with progress_lock:
                completed += 1
                await self._update_progress(
                    context, f"تمت معالجة {completed}/{total} أجزاء"
                )
            return result

        chunk_results = await asyncio.gather(*(_run_chunk(plan) for plan in chunk_plans))
        await self._update_progress(context, "جارٍ دمج النص")

        chunk_segment_lists = [
            _normalize_segments(result.segments, time_offset_seconds=plan.start_ms / 1000)
            for result, plan in zip(chunk_results, chunk_plans, strict=True)
        ]
        primary_starts = [plan.primary_start_ms / 1000 for plan in chunk_plans]
        merged_segments = merge_chunk_segments(
            chunk_segment_lists, primary_start_seconds=primary_starts
        )
        full_text = " ".join(segment.text for segment in merged_segments)

        combined_usage = ProviderUsage(
            input_tokens=sum(r.usage.input_tokens for r in chunk_results),
            output_tokens=sum(r.usage.output_tokens for r in chunk_results),
            total_tokens=sum(r.usage.total_tokens for r in chunk_results),
            cached_input_tokens=sum(r.usage.cached_input_tokens for r in chunk_results),
            # The true recording length, not a sum of per-chunk durations --
            # chunks overlap, so summing would over-count total audio_seconds.
            audio_seconds=chunk_plans[-1].end_ms / 1000,
        )
        representative = TranscriptionResult(
            text=full_text,
            segments=[],
            account=chunk_results[0].account,
            model=chunk_results[0].model,
            response_id=chunk_results[0].response_id,
            usage=combined_usage,
            latency_ms=sum(r.latency_ms for r in chunk_results),
            estimated_cost_usd=sum(r.estimated_cost_usd for r in chunk_results),
        )
        return full_text, merged_segments, representative

    @staticmethod
    async def _update_progress(context: PipelineContext, message: str) -> None:
        context.job.progress_message = message
        await context.session.commit()
