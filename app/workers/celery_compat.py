"""A minimal typed boundary around Celery's untyped task decorator."""

from __future__ import annotations

from collections.abc import Callable
from typing import ParamSpec, TypeVar

from app.workers.celery_app import celery_app

P = ParamSpec("P")
R = TypeVar("R")


def celery_task(*, name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def register(function: Callable[P, R]) -> Callable[P, R]:
        celery_app.task(name=name)(function)
        return function

    return register
