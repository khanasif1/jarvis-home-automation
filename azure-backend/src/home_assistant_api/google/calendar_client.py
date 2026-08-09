"""Google Calendar adapter."""

from __future__ import annotations

from typing import Any, Optional, Protocol

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from home_assistant_api.errors import UpstreamServiceError


class CalendarService(Protocol):
    """The subset of the Google Calendar API surface this adapter uses."""

    def events(self) -> Any:
        ...


def build_calendar_service(credentials: Credentials) -> Resource:
    """Build the real Google Calendar API client (production use)."""

    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


class GoogleCalendarClient:
    """Thin wrapper around a Calendar API service/resource.

    The ``service`` is injected so tests can supply a fake double instead of
    performing a real discovery-document HTTP call.
    """

    def __init__(self, service: CalendarService) -> None:
        self._service = service

    def list_upcoming_events(self, *, time_min_iso: str, max_results: int = 10) -> list[dict[str, Any]]:
        try:
            response = (
                self._service.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min_iso,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError as exc:
            raise UpstreamServiceError("Failed to list Google Calendar events.") from exc
        return list(response.get("items", []))

    def create_event(
        self,
        *,
        summary: str,
        start_iso: str,
        end_iso: str,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
        }
        if description:
            body["description"] = description
        try:
            return self._service.events().insert(calendarId="primary", body=body).execute()
        except HttpError as exc:
            raise UpstreamServiceError("Failed to create a Google Calendar event.") from exc
