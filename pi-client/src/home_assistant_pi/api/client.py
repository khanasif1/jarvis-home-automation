"""HTTP client for the azure-backend voice API.

Uses ``requests`` with a bounded timeout and a small retry budget for
transient network errors. The device token is sent only as an
``Authorization: Bearer <device token>`` header value and is never logged
(no log message, exception string, or ``repr()`` in this module ever
includes the header value or the raw token).

``base_url`` is expected to already include the backend's API prefix (the
``infra`` deployment output ``apiBaseUrl`` is
``https://<function-app>.azurewebsites.net/api``, matching the
``servers: - url: https://{functionAppHost}/api`` entry in
``contracts/openapi.yaml``). Every path built here is therefore relative to
that prefix (``/voice-turn``, ``/health``, ``/reminders/...``) -- it must
never re-add ``/api`` itself.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from .models import ErrorResponse, Reminder, VoiceTurnRequest, VoiceTurnResponse

logger = logging.getLogger(__name__)


class ApiError(RuntimeError):
    """Raised when the backend API call fails or returns an error payload."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    """Thin wrapper around the backend's HTTP voice API."""

    def __init__(
        self,
        base_url: str,
        device_token: str,
        timeout_seconds: float = 15.0,
        retries: int = 2,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._device_token = device_token
        self.timeout_seconds = timeout_seconds
        self.retries = max(0, retries)
        self._session = session or requests.Session()

    def _headers(self, extra: Optional[dict] = None) -> dict:
        headers = {
            "Authorization": "Bearer " + self._device_token,
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                response = self._session.request(
                    method,
                    url,
                    json=json_body,
                    headers=self._headers(extra_headers),
                    timeout=self.timeout_seconds,
                )
                if response.status_code >= 400:
                    error = self._parse_error(response)
                    raise ApiError(
                        f"{error.code}: {error.message}",
                        status_code=response.status_code,
                    )
                if not response.content:
                    return {}
                return response.json()
            except ApiError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "API request to %s failed (attempt %d/%d): %s",
                    path,
                    attempt + 1,
                    self.retries + 1,
                    exc,
                )
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 5))
                    continue
        raise ApiError(f"Request to {path} failed after retries: {last_exc}")

    @staticmethod
    def _parse_error(response: requests.Response) -> ErrorResponse:
        try:
            data = response.json()
            return ErrorResponse.from_dict(data)
        except ValueError:
            return ErrorResponse(
                code=f"http_{response.status_code}",
                message=response.text[:200] if response.text else response.reason,
            )

    def send_voice_turn(self, request: VoiceTurnRequest) -> VoiceTurnResponse:
        """Send a voice turn (text or audio) and return the assistant's reply.

        The request's ``requestId`` (already a UUID unique per logical
        request) doubles as the required ``Idempotency-Key`` header, so a
        genuine retry of the *same* request always carries the same key
        while a new conversation turn always gets a fresh one.
        """
        data = self._request(
            "POST",
            "/voice-turn",
            request.to_dict(),
            extra_headers={"Idempotency-Key": request.request_id},
        )
        return VoiceTurnResponse.from_dict(data)

    def fetch_due_reminders(self, device_id: str) -> list[Reminder]:
        """Fetch reminders that are currently due for ``device_id``."""
        data = self._request("GET", f"/reminders/due?deviceId={device_id}")
        items = data.get("reminders", []) if isinstance(data, dict) else data
        return [Reminder.from_dict(item) for item in items or []]

    def acknowledge_reminder(self, reminder_id: str, device_id: str) -> None:
        """Tell the backend a reminder has been delivered to the user."""
        self._request("POST", f"/reminders/{reminder_id}/ack", {"deviceId": device_id})

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
