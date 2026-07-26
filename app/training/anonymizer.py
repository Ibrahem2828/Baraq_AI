from __future__ import annotations

from typing import Any

from app.utils.text import redact_basic_pii


def anonymize_value(value: Any) -> tuple[Any, dict[str, int]]:
    report = {"emails": 0, "phones": 0, "long_ids": 0}

    def merge(local: dict[str, int]) -> None:
        for key, count in local.items():
            report[key] = report.get(key, 0) + count

    if isinstance(value, str):
        cleaned, local = redact_basic_pii(value)
        merge(local)
        return cleaned, report
    if isinstance(value, list):
        output = []
        for item in value:
            cleaned, local = anonymize_value(item)
            output.append(cleaned)
            merge(local)
        return output, report
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in {"email", "phone", "phone_number", "full_name", "name"}:
                output[key] = "[REDACTED]"
                continue
            cleaned, local = anonymize_value(item)
            output[key] = cleaned
            merge(local)
        return output, report
    return value, report
