from __future__ import annotations

from dataclasses import dataclass

from app.audio.merge import merge_chunk_segments


@dataclass(frozen=True, slots=True)
class _Segment:
    start_seconds: float
    end_seconds: float
    text: str


def test_single_chunk_passes_through_unchanged() -> None:
    segments = [_Segment(0, 5, "a"), _Segment(5, 10, "b")]
    merged = merge_chunk_segments([segments], primary_start_seconds=[0.0])
    assert merged == segments


def test_drops_a_later_chunks_segment_entirely_inside_the_previous_chunks_territory() -> None:
    # Chunk 0 owns [0, 600); chunk 1's audio starts 5s early (overlap), so
    # its first segment (595-598s) duplicates chunk 0's own tail and must
    # be dropped; a segment straddling the boundary (598-604) is kept.
    chunk0 = [_Segment(590, 600, "chunk0 tail")]
    chunk1 = [
        _Segment(595, 598, "duplicate of chunk0 tail"),
        _Segment(598, 604, "straddles the cut"),
        _Segment(604, 610, "clean chunk1 content"),
    ]
    merged = merge_chunk_segments([chunk0, chunk1], primary_start_seconds=[0.0, 600.0])
    assert merged == [
        _Segment(590, 600, "chunk0 tail"),
        _Segment(598, 604, "straddles the cut"),
        _Segment(604, 610, "clean chunk1 content"),
    ]


def test_three_chunks_merge_in_order_with_each_overlap_resolved() -> None:
    chunk0 = [_Segment(0, 300, "a")]
    chunk1 = [_Segment(295, 300, "dup"), _Segment(300, 600, "b")]
    chunk2 = [_Segment(595, 600, "dup2"), _Segment(600, 900, "c")]
    merged = merge_chunk_segments(
        [chunk0, chunk1, chunk2], primary_start_seconds=[0.0, 300.0, 600.0]
    )
    assert [segment.text for segment in merged] == ["a", "b", "c"]
