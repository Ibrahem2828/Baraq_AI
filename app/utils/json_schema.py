from __future__ import annotations

from typing import Any


def to_openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt a Pydantic ``model_json_schema()`` output for OpenAI's
    Structured Outputs strict mode.

    Pydantic only lists a field in an object's "required" array when it has
    no default value -- an ``Optional`` field with ``= None`` is present in
    "properties" but absent from "required". OpenAI's strict mode rejects
    that: every property of every object schema must appear in "required"
    (optionality is expressed only through a nullable type, e.g.
    ``anyOf: [T, {"type": "null"}]``, never through omission), and every
    object must set ``"additionalProperties": false``. This walks the whole
    schema tree (including ``$defs``) and applies both rules in place,
    without changing what values are actually allowed.
    """

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["required"] = list(node["properties"].keys())
                node.setdefault("additionalProperties", False)
            for key in ("properties", "$defs", "definitions"):
                container = node.get(key)
                if isinstance(container, dict):
                    for value in container.values():
                        _walk(value)
            items = node.get("items")
            if isinstance(items, dict):
                _walk(items)
            for key in ("anyOf", "oneOf", "allOf"):
                branch = node.get(key)
                if isinstance(branch, list):
                    for item in branch:
                        _walk(item)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(schema)
    return schema
