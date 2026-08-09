from __future__ import annotations

from home_assistant_api.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    InternalError,
    NotFoundError,
    RateLimitError,
    UpstreamServiceError,
    ValidationError,
)


def test_configuration_error_shape():
    err = ConfigurationError("missing setting")
    assert err.code == "configuration_error"
    assert err.http_status == 500
    assert err.retryable is False
    assert err.message == "missing setting"


def test_validation_error_shape():
    err = ValidationError("bad input")
    assert err.code == "invalid_request"
    assert err.http_status == 400


def test_authentication_error_shape():
    err = AuthenticationError("bad token")
    assert err.code == "unauthorized_device"
    assert err.http_status == 401


def test_authorization_error_shape():
    err = AuthorizationError("forbidden")
    assert err.code == "forbidden_action"
    assert err.http_status == 403


def test_not_found_error_shape():
    err = NotFoundError("missing")
    assert err.code == "not_found"
    assert err.http_status == 404


def test_conflict_error_shape():
    err = ConflictError("dup")
    assert err.code == "conflict_duplicate_request"
    assert err.http_status == 409


def test_rate_limit_error_is_retryable():
    err = RateLimitError("slow down")
    assert err.code == "rate_limited"
    assert err.http_status == 429
    assert err.retryable is True


def test_upstream_service_error_is_retryable():
    err = UpstreamServiceError("boom")
    assert err.code == "upstream_unavailable"
    assert err.http_status == 502
    assert err.retryable is True


def test_internal_error_shape():
    err = InternalError("oops")
    assert err.code == "internal_error"
    assert err.http_status == 500


def test_error_details_are_preserved():
    err = ValidationError("bad", details={"field": "text"})
    assert err.details == {"field": "text"}


def test_error_details_default_to_none():
    err = ValidationError("bad")
    assert err.details is None
