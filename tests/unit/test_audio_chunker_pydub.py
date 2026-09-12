"""Exercises AudioChunker against a real, synthetically generated WAV file
-- pydub decodes/slices/exports WAV via Python's stdlib `wave` module, with
no ffmpeg binary needed, so this runs in any environment (unlike mp3/m4a,
which do need ffmpeg on PATH and are not covered here)."""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from app.audio.chunking import AudioChunker, chunks_from_boundaries, plan_chunk_boundaries

SAMPLE_RATE = 16_000


def _tone(duration_s: float, freq: float = 440.0, amplitude: int = 12_000) -> list[int]:
    n = int(SAMPLE_RATE * duration_s)
    return [int(amplitude * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)) for i in range(n)]


def _silence(duration_s: float) -> list[int]:
    return [0] * int(SAMPLE_RATE * duration_s)


def _wav_bytes(samples: list[int]) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


@pytest.fixture
def two_gap_wav() -> bytes:
    # tone(2s) silence(1.5s) tone(2s) silence(1.5s) tone(2s) = 9s total,
    # with two clear silence gaps a real VAD pass should find.
    samples = _tone(2) + _silence(1.5) + _tone(2) + _silence(1.5) + _tone(2)
    return _wav_bytes(samples)


def test_load_reports_the_real_duration(two_gap_wav: bytes) -> None:
    chunker = AudioChunker(min_silence_len_ms=800, silence_thresh_dbfs=-40)
    audio = chunker.load(two_gap_wav, mime_type="audio/wav")
    assert len(audio) == 9000


def test_detect_silences_finds_both_gaps(two_gap_wav: bytes) -> None:
    chunker = AudioChunker(min_silence_len_ms=800, silence_thresh_dbfs=-40)
    audio = chunker.load(two_gap_wav, mime_type="audio/wav")
    silences = chunker.detect_silences(audio)
    assert len(silences) == 2
    first_start, first_end = silences[0]
    assert 1800 <= first_start <= 2200
    assert 3300 <= first_end <= 3700


def test_export_chunk_produces_a_playable_wav_of_the_right_length(two_gap_wav: bytes) -> None:
    chunker = AudioChunker(min_silence_len_ms=800, silence_thresh_dbfs=-40)
    audio = chunker.load(two_gap_wav, mime_type="audio/wav")
    plan = chunks_from_boundaries(boundaries=[0, 3500, 9000], overlap_ms=500)[0]
    exported = chunker.export_chunk(audio, plan)
    reexported_audio = chunker.load(exported, mime_type="audio/wav")
    assert abs(len(reexported_audio) - (plan.end_ms - plan.start_ms)) <= 5


def test_end_to_end_plan_and_export_covers_the_whole_recording(two_gap_wav: bytes) -> None:
    chunker = AudioChunker(min_silence_len_ms=800, silence_thresh_dbfs=-40)
    audio = chunker.load(two_gap_wav, mime_type="audio/wav")
    silences = chunker.detect_silences(audio)
    boundaries = plan_chunk_boundaries(
        duration_ms=len(audio), target_ms=4000, silences=silences, search_window_ms=1000
    )
    plans = chunks_from_boundaries(boundaries=boundaries, overlap_ms=300)
    assert plans[0].start_ms == 0
    assert plans[-1].end_ms == len(audio)
    for plan in plans:
        exported = chunker.export_chunk(audio, plan)
        assert len(exported) > 0
