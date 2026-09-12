"""Deterministic claim/evidence checks used before an output is persisted."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.errors import ValidationFailure

_TOKEN = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "من",
        "في",
        "على",
        "الى",
        "عن",
        "أن",
        "هذا",
        "هذه",
        "هو",
        "هي",
    }
)


def _terms(text: str) -> set[str]:
    return {
        term.casefold()
        for term in _TOKEN.findall(text)
        if len(term) > 2 and term.casefold() not in _STOP_WORDS
    }


@dataclass(frozen=True, slots=True)
class ClaimEvidenceResult:
    reference_count: int
    supported_references: int
    lexical_support: float

    @property
    def score(self) -> float:
        if self.reference_count == 0:
            return 0.0
        return round((self.supported_references / self.reference_count) * self.lexical_support, 6)


class ClaimEvidenceValidator:
    """Cheap deterministic gate; semantic judging can be layered on for evals.

    It first proves that each cited evidence identifier exists, then requires
    meaningful lexical support from the cited text. It avoids a paid LLM judge
    on every normal production request.
    """

    @staticmethod
    def validate(
        *, claim: str, source_references: list[int], evidence_texts: list[str]
    ) -> ClaimEvidenceResult:
        if not source_references:
            raise ValidationFailure(
                "A source-grounded claim has no evidence", code="missing_evidence"
            )
        if any(reference < 1 or reference > len(evidence_texts) for reference in source_references):
            raise ValidationFailure(
                "A claim references an unavailable evidence record",
                code="invalid_source_reference",
            )
        claim_terms = _terms(claim)
        if not claim_terms:
            raise ValidationFailure("Claim has no verifiable content", code="unverifiable_claim")
        supported = 0
        covered: set[str] = set()
        for reference in source_references:
            evidence_terms = _terms(evidence_texts[reference - 1])
            overlap = claim_terms & evidence_terms
            if overlap:
                supported += 1
                covered.update(overlap)
        lexical_support = len(covered) / len(claim_terms)
        result = ClaimEvidenceResult(
            reference_count=len(source_references),
            supported_references=supported,
            lexical_support=lexical_support,
        )
        if result.supported_references == 0 or result.lexical_support < 0.08:
            raise ValidationFailure(
                "Claim is not supported by its cited evidence",
                code="unsupported_claim",
            )
        return result


def validate_topic_references(*, known_topics: set[str], cited_topics: list[str]) -> None:
    """Reject a reference to a topic absent from the caller's own
    authoritative topic set -- a closed-set grounding check for pipelines
    (Rasheed) whose claims aren't RAG excerpts ClaimEvidenceValidator can
    lexically match against. A caller with no topic data at all (nothing to
    ground against) passes `known_topics=set()`, which is a no-op here.
    """
    if not known_topics:
        return
    for topic in cited_topics:
        if topic.strip().casefold() not in known_topics:
            raise ValidationFailure(
                "A recommendation references a topic absent from the "
                "learner's authoritative topic data",
                code="unsupported_topic_reference",
            )


def transcript_preservation_score(*, raw_transcript: str, cleaned_transcript: str) -> float:
    """Return token retention, rejecting cleanup that materially rewrites audio."""
    raw = _terms(raw_transcript)
    cleaned = _terms(cleaned_transcript)
    if not raw or not cleaned:
        raise ValidationFailure(
            "Transcript cleanup is empty or unverifiable",
            code="transcript_preservation_failed",
        )
    score = len(raw & cleaned) / len(raw)
    if score < 0.55:
        raise ValidationFailure(
            "Transcript cleanup does not preserve enough of the source transcript",
            code="transcript_preservation_failed",
        )
    return round(score, 6)
