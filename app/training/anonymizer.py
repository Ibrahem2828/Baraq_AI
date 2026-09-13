from __future__ import annotations

import re
from typing import Any

from app.utils.text import redact_basic_pii

_SENSITIVE_KEYS = {
    "address",
    "email",
    "full_name",
    "institution",
    "name",
    "owner_id",
    "phone",
    "phone_number",
    "source_id",
    "user_id",
    "username",
}
_NATURAL_LANGUAGE = re.compile(r"[A-Za-z\u0600-\u06ff]")


def anonymize_value(value: Any) -> tuple[Any, dict[str, int]]:
    report = {"emails": 0, "phones": 0, "long_ids": 0, "unverified_free_text": 0}

    def merge(local: dict[str, int]) -> None:
        for key, count in local.items():
            report[key] = report.get(key, 0) + count

    if isinstance(value, str):
        cleaned, local = redact_basic_pii(value)
        merge(local)
        if _NATURAL_LANGUAGE.search(cleaned):
            # Regex redaction cannot prove that prose contains no person,
            # address, school, or username. Keep the content for a reviewer,
            # but never label it proven-anonymous automatically.
            report["unverified_free_text"] += 1
        return cleaned, report
    if isinstance(value, list):
        list_output = []
        for item in value:
            cleaned, local = anonymize_value(item)
            list_output.append(cleaned)
            merge(local)
        return list_output, report
    if isinstance(value, dict):
        dict_output: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in _SENSITIVE_KEYS:
                dict_output[key] = "[REDACTED]"
                report["redacted_sensitive_fields"] = (
                    report.get("redacted_sensitive_fields", 0) + 1
                )
                continue
            cleaned, local = anonymize_value(item)
            dict_output[key] = cleaned
            merge(local)
        return dict_output, report
    return value, report
