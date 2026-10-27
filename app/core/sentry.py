"""Sentry configuration that cannot carry private study content.

Prompts, source text and model output live in local variables and request
bodies, so neither is ever attached to an event: frames are sent without their
variables and request payloads are dropped before the event leaves the process.
"""

from __future__ import annotations

from typing import Any

_REDACTED = "[redacted]"
_REQUEST_FIELDS_DROPPED = ("data", "cookies", "query_string")


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    request = event.get("request")
    if isinstance(request, dict):
        for field in _REQUEST_FIELDS_DROPPED:
            if field in request:
                request[field] = _REDACTED
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                name: (_REDACTED if name.lower() in _SENSITIVE_HEADERS else value)
                for name, value in headers.items()
            }
    for exception in (event.get("exception") or {}).get("values") or []:
        for frame in (exception.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    for crumb in (event.get("breadcrumbs") or {}).get("values") or []:
        if isinstance(crumb, dict) and crumb.get("data"):
            crumb["data"] = _REDACTED
    return event


_SENSITIVE_HEADERS = frozenset(
    {"authorization", "cookie", "x-signature", "x-baraq-signature", "x-content-sha256"}
)

SENTRY_OPTIONS: dict[str, Any] = {
    "send_default_pii": False,
    "include_local_variables": False,
    "max_request_body_size": "never",
    "before_send": scrub_event,
}
