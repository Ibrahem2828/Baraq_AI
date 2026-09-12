"""Baraq_MD_Blueprint 02_AI_PLATFORM.md §8.2/§8.3: ~10 minute target chunks
cut near a silence boundary, small overlap to avoid losing a boundary word.

The boundary-planning functions are pure (plain ints, a silence-interval
list) -- no audio decoding, so no ffmpeg dependency for these. AudioChunker
itself is exercised in test_audio_chunker_pydub.py against a real
synthetic WAV file (pydub handles WAV via Python's stdlib `wave` module,
no ffmpeg needed for that one format)."""

from __future__ import annotations

from app.audio.chunking import chunks_from_boundaries, plan_chunk_boundaries


def test_short_audio_is_a_single_chunk() -> None:
    boundaries = plan_chunk_boundaries(
        duration_ms=5 * 60_000, target_ms=10 * 60_000, silences=[], search_window_ms=30_000
    )
    assert boundaries == [0, 5 * 60_000]


def test_long_audio_with_no_silence_cuts_blindly_at_the_target() -> None:
    duration = 25 * 60_000
    boundaries = plan_chunk_boundaries(
        duration_ms=duration, target_ms=10 * 60_000, silences=[], search_window_ms=30_000
    )
    assert boundaries == [0, 10 * 60_000, 20 * 60_000, duration]


def test_a_nearby_silence_is_preferred_over_the_blind_target() -> None:
    # Target is exactly 10:00; a silence a few seconds earlier should win.
    ten_minutes = 10 * 60_000
    silence = (ten_minutes - 4_000, ten_minutes - 2_000)  # midpoint = target - 3000ms
    boundaries = plan_chunk_boundaries(
        duration_ms=22 * 60_000,
        target_ms=ten_minutes,
        silences=[silence],
        search_window_ms=30_000,
    )
    assert boundaries[1] == ten_minutes - 3_000


def test_a_distant_silence_outside_the_search_window_is_ignored() -> None:
    ten_minutes = 10 * 60_000
    far_silence = (ten_minutes - 60_000, ten_minutes - 58_000)  # 58s away, window is 30s
    boundaries = plan_chunk_boundaries(
        duration_ms=22 * 60_000,
        target_ms=ten_minutes,
        silences=[far_silence],
        search_window_ms=30_000,
    )
    assert boundaries[1] == ten_minutes


def test_chunks_from_boundaries_overlaps_every_chunk_but_the_first() -> None:
    boundaries = [0, 600_000, 1_200_000, 1_800_000]
    plans = chunks_from_boundaries(boundaries=boundaries, overlap_ms=5_000)
    assert [p.start_ms for p in plans] == [0, 595_000, 1_195_000]
    assert [p.end_ms for p in plans] == [600_000, 1_200_000, 1_800_000]
    assert [p.primary_start_ms for p in plans] == [0, 600_000, 1_200_000]


def test_chunks_from_boundaries_never_goes_negative_at_the_start() -> None:
    plans = chunks_from_boundaries(boundaries=[0, 2_000], overlap_ms=5_000)
    assert plans[0].start_ms == 0
