"""Explicit error taxonomy shared by every layer of the backend.

Every raised error maps to exactly one HTTP status code and one machine
readable ``code`` matching the ``error-response.json`` contract
(``^[a-z][a-z0-9_]{2,63}$``). Handlers must catch these specific types; they
must never swallow unexpected exceptions silently.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class AppError(Exception):
    """Base class for every error the backend raises intentionally.

    Attributes:
        code: Machine readable error code returned to callers.
        http_status: HTTP status code the API layer must respond with.
        message: Human readable message safe to return to a client.
        retryable: Whether retrying the same request might succeed.
        details: Optional structured context (never secrets or tokens).
    """

    code: str = "internal_error"
    http_status: int = 500
    retryable: bool = False

    def __init__(self, message: str, *, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details) if details else None


class ConfigurationError(AppError):
    """Raised when required configuration or credentials are missing.

    This is always an explicit failure: the backend never falls back to a
    silent no-op when a dependency (Azure OpenAI, Speech, Google) is
    unconfigured.
    """

    code = "configuration_error"
    http_status = 500
    retryable = False


class ValidationError(AppError):
    """Raised when a request fails schema or business-rule validation."""

    code = "invalid_request"
    http_status = 400
    retryable = False


class AuthenticationError(AppError):
    """Raised when a device token is missing, malformed, or invalid."""

    code = "unauthorized_device"
    http_status = 401
    retryable = False


class AuthorizationError(AppError):
    """Raised when an authenticated caller may not perform an action."""

    code = "forbidden_action"
    http_status = 403
    retryable = False


class NotFoundError(AppError):
    """Raised when a referenced resource does not exist."""

    code = "not_found"
    http_status = 404
    retryable = False


class ConflictError(AppError):
    """Raised when an idempotency key is replayed with a different body."""

    code = "conflict_duplicate_request"
    http_status = 409
    retryable = False


class RateLimitError(AppError):
    """Raised when a caller exceeds an enforced request budget."""

    code = "rate_limited"
    http_status = 429
    retryable = True


class UpstreamServiceError(AppError):
    """Raised when a downstream dependency (Speech, OpenAI, Google) fails."""

    code = "upstream_unavailable"
    http_status = 502
    retryable = True


class InternalError(AppError):
    """Raised for truly unexpected conditions surfaced as a generic 500."""

    code = "internal_error"
    http_status = 500
    retryable = False
