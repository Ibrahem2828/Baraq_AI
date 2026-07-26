from __future__ import annotations

from app.schemas.common import Citation


def resolve_reference_numbers(numbers: list[int], citations: list[Citation]) -> list[Citation]:
    resolved: list[Citation] = []
    seen: set[int] = set()
    for number in numbers:
        if number in seen or number < 1 or number > len(citations):
            continue
        resolved.append(citations[number - 1])
        seen.add(number)
    return resolved
