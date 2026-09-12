"""Merge per-chunk transcript segments into one ordered, deduplicated
timeline. Baraq_MD_Blueprint 02_AI_PLATFORM.md §8.2: "Merge + overlap
de-duplication" -- pure function over plain segment-like objects, no audio
involved, so it's independently unit-testable.

Each chunk's audio was widened backward by `overlap_ms` (see
app/audio/chunking.chunks_from_boundaries) so a word right at a cut
boundary appears in full in at least one chunk. That means a chunk's own
transcription can include a leading stretch that duplicates the *previous*
chunk's tail -- this drops that duplicated stretch by time, not by fuzzy
text matching: any segment whose absolute end falls at or before this
chunk's own (non-overlap) primary start already belongs to the previous
chunk's territory.
"""

from __future__ import annotations

from typing import Protocol


class _HasTiming(Protocol):
    """Read-only on purpose (a property, not a plain attribute) so a frozen
    dataclass or pydantic model -- neither writable -- still satisfies it;
    this function only ever reads these fields."""

    @property
    def start_seconds(self) -> float: ...

    @property
    def end_seconds(self) -> float: ...


def merge_chunk_segments[SegmentT: _HasTiming](
    chunk_segments: list[list[SegmentT]], *, primary_start_seconds: list[float]
) -> list[SegmentT]:
    """`chunk_segments[i]` is chunk i's own segments (already offset to
    absolute time); `primary_start_seconds[i]` is chunk i's own
    (non-overlap) responsibility start -- 0.0 for the first chunk. A
    segment entirely inside the overlap zone the previous chunk already
    owns is dropped."""
    merged: list[SegmentT] = []
    for segments, primary_start in zip(chunk_segments, primary_start_seconds, strict=True):
        for segment in segments:
            if segment.end_seconds <= primary_start:
                continue
            merged.append(segment)
    return merged
