from __future__ import annotations

import re

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_LONG_ID = re.compile(r"\b\d{8,}\b")


def normalize_arabic_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def redact_basic_pii(text: str) -> tuple[str, dict[str, int]]:
    report = {"emails": 0, "phones": 0, "long_ids": 0}

    def replace_email(_: re.Match[str]) -> str:
        report["emails"] += 1
        return "[EMAIL_REDACTED]"

    def replace_phone(_: re.Match[str]) -> str:
        report["phones"] += 1
        return "[PHONE_REDACTED]"

    def replace_id(_: re.Match[str]) -> str:
        report["long_ids"] += 1
        return "[ID_REDACTED]"

    text = _EMAIL.sub(replace_email, text)
    text = _PHONE.sub(replace_phone, text)
    text = _LONG_ID.sub(replace_id, text)
    return text, report
