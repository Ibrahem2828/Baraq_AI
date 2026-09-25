"""Which part of the selected sources a request is about.

One rule for every character that reads sources, so "focus on unit two"
means the same thing to Fahes, Kholasa and Khota:

1. Units named in the learner's instructions or topic: an even sample of
   exactly those units (app/rag/outline.py). A unit the source does not
   have falls back to the whole source, with a warning.
2. A topic, or instructions that are about content: similarity search on
   that text. Instructions are often about style ("make it harder"), which
   matches nothing -- too few matches falls back to the whole source.
3. Nothing: an even sample of the whole source, front matter excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.rag.outline import requested_units, unit_label
from app.rag.retriever import RAGContext, RAGRetriever

#: Fewer similarity matches than this means the text did not name content.
MIN_FOCUSED_CHUNKS = 3


@dataclass(slots=True)
class ScopedContext:
    rag: RAGContext
    units: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


async def scoped_context(
    retriever: RAGRetriever,
    *,
    user_id: str,
    project_id: str,
    source_ids: list[str],
    source_versions: dict[str, str],
    routing_key: str,
    focus: str | None,
    instructions: str | None,
    language: str = "ar",
) -> ScopedContext:
    units = requested_units(instructions) | requested_units(focus)
    if units:
        rag, found = await retriever.retrieve_scoped(
            user_id=user_id,
            project_id=project_id,
            source_ids=source_ids,
            source_versions=source_versions,
            units=units,
        )
        if found:
            return ScopedContext(rag=rag, units=units)
        names = ", ".join(unit_label(unit, language) for unit in sorted(units))
        return ScopedContext(rag=rag, warnings=[f"requested_unit_not_found: {names}"])
    query = focus or instructions
    if query:
        rag = await retriever.retrieve(
            user_id=user_id,
            project_id=project_id,
            source_ids=source_ids,
            source_versions=source_versions,
            query=query,
            routing_key=routing_key,
        )
        if len(rag.citations) >= MIN_FOCUSED_CHUNKS or (focus and rag.citations):
            return ScopedContext(rag=rag)
    rag = await retriever.retrieve_across(
        user_id=user_id,
        project_id=project_id,
        source_ids=source_ids,
        source_versions=source_versions,
    )
    return ScopedContext(rag=rag)
