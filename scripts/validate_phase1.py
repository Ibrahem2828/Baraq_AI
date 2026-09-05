"""Run Phase 1 validation and make unrun critical gates explicit.

Set PHASE1_TEST_DATABASE_SYNC_URL to an empty disposable PostgreSQL database
before invoking this script if migration verification is required.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    command: list[str]
    critical: bool = True


def run(check: Check, *, env: dict[str, str] | None = None) -> bool:
    result = subprocess.run(check.command, check=False, env=env)
    print(f"{check.name}: {'PASS' if result.returncode == 0 else 'FAIL'}")
    return result.returncode == 0


def main() -> int:
    checks = [
        Check("compile", [sys.executable, "-m", "compileall", "app", "alembic", "scripts"]),
        Check("tests", [sys.executable, "-m", "pytest"]),
        Check("lint", [sys.executable, "-m", "ruff", "check", "."]),
        Check("typecheck", [sys.executable, "-m", "mypy", "."]),
        Check("package", [sys.executable, "scripts/validate_package.py"]),
        Check("openapi", [sys.executable, "scripts/export_openapi.py"]),
        Check(
            "django_hmac_interop",
            [sys.executable, "scripts/validate_django_hmac_interop.py"],
        ),
    ]
    passed = all([run(check) for check in checks])
    database_url = os.environ.get("PHASE1_TEST_DATABASE_SYNC_URL")
    if not database_url:
        print("migration: NOT RUN (PHASE1_TEST_DATABASE_SYNC_URL is not configured)")
        return 1
    migration_check = Check(
        "migration",
        [sys.executable, "-m", "alembic", "-x", f"database_url={database_url}", "upgrade", "head"],
    )
    migration_env = os.environ.copy()
    migration_env["DATABASE_SYNC_URL"] = database_url
    return 0 if passed and run(migration_check, env=migration_env) else 1


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
