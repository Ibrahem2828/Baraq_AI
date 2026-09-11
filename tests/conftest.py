"""Global test-mode defaults independent from a developer's local `.env`."""

from __future__ import annotations

import os

# Contract and service tests import `app.main`; they must not accidentally
# inherit a developer's interactive Lab mode from `.env`.  Lab-specific tests
# instantiate their local adapters explicitly and remain unaffected.
os.environ["BARAQ_RUNTIME_MODE"] = "service"
