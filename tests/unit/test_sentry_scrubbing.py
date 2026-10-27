from __future__ import annotations

from typing import Any

from app.core.sentry import SENTRY_OPTIONS, scrub_event


def event() -> dict[str, Any]:
    return {
        "request": {
            "url": "http://ai-api/internal/v1/jobs",
            "data": {"input": "the student's private essay"},
            "cookies": {"session": "abc"},
            "query_string": "user_id=42",
            "headers": {"Authorization": "Bearer x", "X-Signature": "sig", "Accept": "json"},
        },
        "exception": {
            "values": [
                {"stacktrace": {"frames": [{"function": "run", "vars": {"prompt": "secret text"}}]}}
            ]
        },
        "breadcrumbs": {"values": [{"message": "db", "data": {"sql": "select source_text"}}]},
    }


def test_request_bodies_cookies_and_sensitive_headers_are_removed() -> None:
    scrubbed = scrub_event(event())
    request = scrubbed["request"]

    assert request["data"] == "[redacted]"
    assert request["cookies"] == "[redacted]"
    assert request["query_string"] == "[redacted]"
    assert request["headers"]["Authorization"] == "[redacted]"
    assert request["headers"]["X-Signature"] == "[redacted]"
    assert request["headers"]["Accept"] == "json"
    assert request["url"] == "http://ai-api/internal/v1/jobs"


def test_stack_frames_lose_their_local_variables() -> None:
    frame = scrub_event(event())["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "vars" not in frame
    assert frame["function"] == "run"


def test_breadcrumb_payloads_are_dropped() -> None:
    assert scrub_event(event())["breadcrumbs"]["values"][0]["data"] == "[redacted]"


def test_an_event_without_any_of_those_sections_passes_through() -> None:
    assert scrub_event({"message": "x"}) == {"message": "x"}


def test_the_sdk_is_configured_not_to_collect_content_in_the_first_place() -> None:
    assert SENTRY_OPTIONS["send_default_pii"] is False
    assert SENTRY_OPTIONS["include_local_variables"] is False
    assert SENTRY_OPTIONS["max_request_body_size"] == "never"
    assert SENTRY_OPTIONS["before_send"] is scrub_event
