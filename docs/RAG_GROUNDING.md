# RAG and grounding

Fahes and Kholasa use `SELECTED_SOURCES_ONLY`. At job acceptance Django
manifests freeze `source_id -> content_sha256`; ingestion and retrieval reject
a changed manifest and query by user, allowed source IDs and exact hashes.

Retrieved citations carry evidence ID, source hash, chunk, page, section,
semantic score, lexical score and rerank score. The answerability gate rejects
empty or insufficient source context before generation. `ClaimEvidenceValidator`
checks citation existence and deterministic lexical evidence support for quiz
claims and summary flashcards. These scores are calculated from evidence, never
filled with decorative constants.

This deterministic validator is a first gate, not a claim of universal
hallucination elimination. Evaluation suites may add selective semantic judging
for difficult claims.
