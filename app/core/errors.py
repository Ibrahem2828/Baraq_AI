from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    """Normalized error taxonomy (spec Appendix B). One vocabulary for
    providers, the router, and the job processor instead of ad hoc strings."""

    INVALID_CONTRACT = "invalid_contract"
    AUTH_FAILED = "auth_failed"
    SOURCE_ACCESS_DENIED = "source_access_denied"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_INVALID_OUTPUT = "provider_invalid_output"
    RAG_NO_CONTEXT = "rag_no_context"
    BUDGET_EXCEEDED = "budget_exceeded"
    DELIVERY_FAILED = "delivery_failed"


# HTTP-style status codes that are transient by nature (Appendix B:
# "429/5xx/timeout retryable; 400 schema/input and 401/403 not").
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_NON_RETRYABLE_STATUS = frozenset({400, 401, 403})


def classify_provider_status(status: int | None) -> tuple[ErrorCategory, bool]:
    """Map a raw SDK/HTTP status code to (category, retryable).

    Unknown status codes (SDK network errors report ``None``) are treated as
    transient/retryable rather than silently swallowed.
    """
    if status == 429:
        return ErrorCategory.PROVIDER_RATE_LIMITED, True
    if status in {401, 403}:
        return ErrorCategory.AUTH_FAILED, False
    if status == 400:
        return ErrorCategory.INVALID_CONTRACT, False
    if status in _NON_RETRYABLE_STATUS:
        return ErrorCategory.AUTH_FAILED, False
    if status is None or status in _RETRYABLE_STATUS:
        return ErrorCategory.PROVIDER_UNAVAILABLE, True
    return ErrorCategory.PROVIDER_UNAVAILABLE, True


@dataclass(slots=True)
class AppError(Exception):
    message: str
    code: str = "application_error"
    status_code: int = 400
    details: dict[str, Any] | None = None
    retryable: bool = False

    def __str__(self) -> str:
        return self.message


class AuthenticationError(AppError):
    def __init__(
        self, message: str = "Authentication failed", *, code: str = "authentication_error"
    ) -> None:
        super().__init__(message, code, 401)


class AuthorizationError(AppError):
    def __init__(
        self, message: str = "Permission denied", *, code: str = "authorization_error"
    ) -> None:
        super().__init__(message, code, 403)


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, "not_found", 404)


class ConflictError(AppError):
    def __init__(self, message: str, code: str = "conflict") -> None:
        super().__init__(message, code, 409)


class ProviderError(AppError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, 502, details, retryable)


class ValidationFailure(AppError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "output_validation_failed",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, 422, details, False)


class RateLimitError(AppError):
    def __init__(
        self, message: str = "Too many requests", *, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            message,
            code="rate_limit_exceeded",
            status_code=429,
            details=details or {},
            retryable=True,
        )
