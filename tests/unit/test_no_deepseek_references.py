"""CI gate for spec Appendix C: zero DeepSeek references outside an explicit,
clearly-marked historical record. This is a Hard Fail item -- any match here
must fail the build, not just this test.

A whole file is exempt from the line-by-line scan only if it opens with an
explicit historical/superseded/deprecated banner in its first few lines
(the same convention already used by docs/PHASE_1_EXECUTION_REPORT.md) --
that marks the entire document as a preserved historical record, not active
guidance. Active code, config, and current docs get no such exemption.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ["app", "config", "scripts", "docs", ".env.example"]
PATTERN = re.compile(r"deepseek", re.IGNORECASE)
FILE_LEVEL_HISTORICAL_BANNER = re.compile(
    r"deprecated|superseded|historical|تاريخي|منسوخ", re.IGNORECASE
)
# For a mixed-content doc (a live section referencing a past decision inline,
# rather than a fully-historical file), the same-line allowance still
# applies so a sentence can say "X was removed" without failing the gate.
ALLOWED_LINE_PATTERN = re.compile(
    r"historical|changelog|removed|superseded|deprecated|"
    r"تاريخي|تاريخية|أزيل|إزالة|استبدال|سابق|منسوخ",
    re.IGNORECASE,
)


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCANNED_DIRS:
        path = REPO_ROOT / entry
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return files


def test_no_deepseek_reference_outside_an_explicit_historical_note() -> None:
    violations: list[str] = []
    for file_path in _iter_files():
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if FILE_LEVEL_HISTORICAL_BANNER.search(text[:500]):
            continue  # whole file is a preserved historical record
        for line_number, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line) and not ALLOWED_LINE_PATTERN.search(line):
                relative = file_path.relative_to(REPO_ROOT)
                violations.append(f"{relative}:{line_number}: {line.strip()}")
    message = "DeepSeek reference(s) found outside an explicit historical note:\n" + "\n".join(
        violations
    )
    assert not violations, message
