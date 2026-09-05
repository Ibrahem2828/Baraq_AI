from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_idempotency_scope(*, user_id: str, client_job_id: str) -> str:
    """Hash the contract's idempotency boundary without storing it as a secret."""
    return hashlib.sha256(f"{user_id}:{client_job_id}".encode()).hexdigest()
