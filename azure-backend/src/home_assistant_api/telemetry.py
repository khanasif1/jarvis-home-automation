"""Structured telemetry for the backend.

The Azure Functions Python worker forwards standard library log records to
Application Insights automatically when ``APPINSIGHTS_INSTRUMENTATIONKEY`` or
``APPLICATIONINSIGHTS_CONNECTION_STRING`` is configured on the Function App,
so this module intentionally avoids adding a heavyweight SDK dependency. It
gives the rest of the codebase a small, explicit, structured logging surface
instead of scattering ad-hoc ``logging.getLogger(__name__)`` calls.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

_LOGGER_NAME = "home_assistant_api"


class TelemetryClient:
    """Thin structured-logging wrapper used across the backend.

    Every method logs through the standard library ``logging`` module with
    ``extra`` properties so a single log record carries correlation
    identifiers without ever including secrets, tokens, or raw audio.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger(_LOGGER_NAME)

    def track_event(self, name: str, properties: Optional[Mapping[str, Any]] = None) -> None:
        self._logger.info("event=%s properties=%s", name, _safe(properties))

    def track_dependency(
        self,
        name: str,
        *,
        duration_ms: float,
        success: bool,
        properties: Optional[Mapping[str, Any]] = None,
    ) -> None:
        level = logging.INFO if success else logging.WARNING
        self._logger.log(
            level,
            "dependency=%s duration_ms=%.3f success=%s properties=%s",
            name,
            duration_ms,
            success,
            _safe(properties),
        )

    def track_exception(
        self,
        exc: BaseException,
        properties: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._logger.exception(
            "exception=%s properties=%s",
            exc.__class__.__name__,
            _safe(properties),
            exc_info=exc,
        )


_REDACTED_KEYS = {"authorization", "token", "audiobase64", "audio_base64", "password", "secret"}


def _safe(properties: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Redact obviously sensitive keys before they reach any log sink."""

    if not properties:
        return {}
    redacted: dict[str, Any] = {}
    for key, value in properties.items():
        if key.lower() in _REDACTED_KEYS:
            redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted


_default_client: Optional[TelemetryClient] = None


def get_telemetry_client() -> TelemetryClient:
    """Return a process-wide singleton telemetry client."""

    global _default_client
    if _default_client is None:
        _default_client = TelemetryClient()
    return _default_client
