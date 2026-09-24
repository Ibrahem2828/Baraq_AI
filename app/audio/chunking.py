"""Long-recording chunking for Sada, per Baraq_MD_Blueprint 02_AI_PLATFORM.md
§8.2/§8.3: "target chunk ~10 minutes, cut near a silence/sentence boundary
close to the mark, a small overlap where needed to avoid losing a word at
the cut, real start_seconds/end_seconds kept, never merge text without
timestamps."

Boundary planning (`plan_chunk_boundaries`/`chunks_from_boundaries`) is pure
-- it operates on plain floats/ints and a list of detected silence
intervals, with no audio decoding, so it's fully unit-testable without
ffmpeg or any audio library. `AudioChunker` is the thin adapter that
actually decodes/slices audio via pydub; pydub handles WAV natively via
Python's stdlib `wave` module (no ffmpeg needed), but needs a real ffmpeg
binary on PATH to decode compressed formats (mp3, m4a, ogg, ...), which
production images must install.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from pydub import AudioSegment
from pydub.silence import detect_silence


@dataclass(frozen=True, slots=True)
class ChunkPlan:
    index: int
    start_ms: int
    end_ms: int
    # Where this chunk's *own* (non-overlap) responsibility begins -- equal
    # to start_ms for the first chunk, otherwise the target boundary before
    # the overlap was subtracted. Used by the merge step to know which
    # leading portion of this chunk duplicates the previous chunk's tail.
    primary_start_ms: int


def plan_chunk_boundaries(
    *,
    duration_ms: int,
    target_ms: int,
    silences: list[tuple[int, int]],
    search_window_ms: int,
) -> list[int]:
    """Return cut points [0, c1, c2, ..., duration_ms]. Each interior cut
    snaps to the nearest detected silence interval's midpoint within
    `search_window_ms` of the raw target multiple; with none found nearby,
    it falls back to a blunt cut at the target -- "target, not blind
    chopping" still degrades safely instead of refusing to chunk at all.
    """
    if duration_ms <= target_ms:
        return [0, duration_ms]

    boundaries = [0]
    cursor = target_ms
    while cursor < duration_ms:
        best_cut = cursor
        best_distance = search_window_ms + 1
        for silence_start, silence_end in silences:
            midpoint = (silence_start + silence_end) // 2
            distance = abs(midpoint - cursor)
            if distance <= search_window_ms and distance < best_distance:
                best_cut = midpoint
                best_distance = distance
        # Never emit a cut at or before the previous boundary (a silence
        # right after the last cut could otherwise produce a zero-length
        # or out-of-order chunk).
        if best_cut <= boundaries[-1]:
            best_cut = cursor
        boundaries.append(best_cut)
        cursor = best_cut + target_ms
    if boundaries[-1] < duration_ms:
        boundaries.append(duration_ms)
    return boundaries


def chunks_from_boundaries(*, boundaries: list[int], overlap_ms: int) -> list[ChunkPlan]:
    """Turn cut points into chunks, extending each chunk's start backward by
    `overlap_ms` (except the first) so a word right at a cut appears in
    full in at least one chunk's audio."""
    plans: list[ChunkPlan] = []
    for index in range(len(boundaries) - 1):
        primary_start = boundaries[index]
        end = boundaries[index + 1]
        start = max(0, primary_start - overlap_ms) if index > 0 else primary_start
        plans.append(
            ChunkPlan(index=index, start_ms=start, end_ms=end, primary_start_ms=primary_start)
        )
    return plans


_MIME_TO_PYDUB_FORMAT = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mp4": "mp4",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/aac": "aac",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}


def _pydub_format_for(mime_type: str) -> str | None:
    return _MIME_TO_PYDUB_FORMAT.get(mime_type.split(";")[0].strip().lower())


def audio_upload_filename(mime_type: str, *, stem: str = "audio") -> str:
    """A filename whose extension names the audio format.

    OpenAI infers the format from the upload's filename extension alone, so
    the learner's title (e.g. "تسجيل صوتي", no extension) was rejected as
    "Unsupported file format". ASCII stem, extension from the MIME type.
    """
    return f"{stem}.{_pydub_format_for(mime_type) or 'wav'}"


class AudioChunker:
    """Thin pydub adapter: decode once, detect silences once, export each
    planned chunk as WAV bytes (OpenAI's transcription API infers format
    from the filename extension it's given, so a uniform WAV export works
    regardless of the source's original container/codec)."""

    def __init__(
        self,
        *,
        min_silence_len_ms: int,
        silence_thresh_dbfs: int,
    ) -> None:
        self.min_silence_len_ms = min_silence_len_ms
        self.silence_thresh_dbfs = silence_thresh_dbfs

    def load(self, content: bytes, *, mime_type: str) -> AudioSegment:
        return AudioSegment.from_file(io.BytesIO(content), format=_pydub_format_for(mime_type))

    def detect_silences(self, audio: AudioSegment) -> list[tuple[int, int]]:
        intervals = detect_silence(
            audio,
            min_silence_len=self.min_silence_len_ms,
            silence_thresh=self.silence_thresh_dbfs,
        )
        return [(int(start), int(end)) for start, end in intervals]

    def export_chunk(self, audio: AudioSegment, plan: ChunkPlan) -> bytes:
        segment = audio[plan.start_ms : plan.end_ms]
        buffer = segment.export(format="wav")
        return bytes(buffer.read())
