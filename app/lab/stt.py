"""Local speech-to-text adapter for the standalone Lab.

The module imports Faster-Whisper only when Sada is invoked.  This keeps the
text-only Lab usable on machines that have not installed the optional local
STT dependency, and it never substitutes a cloud transcription provider.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import ValidationFailure


@dataclass(frozen=True, slots=True)
class LocalTranscript:
    text: str
    segments: list[dict[str, Any]]
    duration_seconds: float | None


class LocalWhisperAdapter:
    def __init__(self, *, model_name: str, language: str) -> None:
        self.model_name = model_name
        self.language = language
        self._model: Any | None = None

    async def transcribe(
        self, *, filename: str, content: bytes, language: str | None
    ) -> LocalTranscript:
        return await asyncio.to_thread(
            self._transcribe_sync,
            filename=filename,
            content=content,
            language=language or self.language,
        )

    def _transcribe_sync(self, *, filename: str, content: bytes, language: str) -> LocalTranscript:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValidationFailure(
                "Local Whisper is unavailable. Install the Lab extra before running Sada.",
                code="local_whisper_unavailable",
            ) from exc

        if self._model is None:
            # CPU is the safest default for a portable Lab.  Faster-Whisper
            # selects the optimized execution path available on the machine.
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

        suffix = Path(filename).suffix or ".audio"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        try:
            segments_iter, _info = self._model.transcribe(
                str(temporary_path),
                language=language,
                vad_filter=True,
            )
            segments: list[dict[str, Any]] = []
            for segment in segments_iter:
                text = str(segment.text).strip()
                if text:
                    segments.append(
                        {
                            "start_seconds": float(segment.start),
                            "end_seconds": float(segment.end),
                            "text": text,
                            "speaker": None,
                            "confidence": None,
                        }
                    )
        except Exception as exc:
            raise ValidationFailure(
                "Local Whisper could not transcribe this audio file",
                code="local_whisper_transcription_failed",
            ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)

        if not segments:
            raise ValidationFailure("No speech was detected in the audio", code="empty_transcript")
        return LocalTranscript(
            text=" ".join(item["text"] for item in segments),
            segments=segments,
            duration_seconds=float(segments[-1]["end_seconds"]),
        )
