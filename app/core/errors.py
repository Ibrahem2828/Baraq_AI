from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message, "authentication_error", 401)


class AuthorizationError(AppError):
    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(message, "authorization_error", 403)


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
    def __init__(self, message: str = "Too many requests", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="rate_limit_exceeded",
            status_code=429,
            details=details or {},
            retryable=True,
        )
