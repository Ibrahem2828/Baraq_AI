from __future__ import annotations

from typing import Any


def suspicious_source_flags(source_ids: list[str]) -> list[dict[str, Any]]:
    """Operational, non-content-bearing records of untrusted-source signals."""
    return [
        {
            "type": "prompt_injection",
            "severity": "medium",
            "source_id": source_id,
            "detector": "rag_untrusted_source_guard",
            "metadata": {},
        }
        for source_id in source_ids
    ]
