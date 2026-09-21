"""Regression gates for dependency floors raised by release vulnerability audits."""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _dependencies(group: str | None = None) -> set[str]:
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    if group is None:
        return set(document["project"]["dependencies"])
    return set(document["project"]["optional-dependencies"][group])


def test_runtime_dependency_floors_include_the_security_fixed_releases() -> None:
    dependencies = _dependencies()

    assert "cryptography>=50.0.0,<51" in dependencies
    assert "Pillow>=12.3.0,<13.0" in dependencies


def test_async_test_stack_is_compatible_with_the_fixed_pytest_release() -> None:
    development_dependencies = _dependencies("dev")

    assert "pytest>=9.0.3,<10.0" in development_dependencies
    assert "pytest-asyncio>=1.0,<2.0" in development_dependencies
