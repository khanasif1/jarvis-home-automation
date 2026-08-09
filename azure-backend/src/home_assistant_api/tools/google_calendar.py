"""Google Calendar tools exposed to the assistant.

Every function requires the device to have completed Google OAuth; if it
has not, :class:`~home_assistant_api.errors.ConfigurationError` propagates
and the orchestrator surfaces it as a clearly failed action, never as a
silently empty result.
"""

from __future__ import annotations

from typing import Any

from home_assistant_api.errors import ConfigurationError, ValidationError
from home_assistant_api.google.calendar_client import GoogleCalendarClient
from home_assistant_api.time_utils import to_iso8601, utc_now
from home_assistant_api.tools import ToolContext


def _client_for(context: ToolContext) -> GoogleCalendarClient:
    if context.credential_store is None:
        raise ConfigurationError(
            "Google Calendar is not configured for this backend deployment."
        )
    credentials = context.credential_store.get_credentials(context.device_id)
    service = context.calendar_service_factory(credentials)
    return GoogleCalendarClient(service)


def list_calendar_events(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    client = _client_for(context)
    time_min_iso = arguments.get("time_min") or to_iso8601(utc_now())
    max_results = int(arguments.get("max_results", 10))
    events = client.list_upcoming_events(time_min_iso=time_min_iso, max_results=max_results)
    return {
        "events": [
            {
                "id": event.get("id"),
                "summary": event.get("summary"),
                "start": event.get("start"),
                "end": event.get("end"),
            }
            for event in events
        ]
    }


def create_calendar_event(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    client = _client_for(context)
    summary = _require_str(arguments, "summary")
    start_iso = _require_str(arguments, "start")
    end_iso = _require_str(arguments, "end")
    description = arguments.get("description")
    event = client.create_event(
        summary=summary, start_iso=start_iso, end_iso=end_iso, description=description
    )
    return {"id": event.get("id"), "htmlLink": event.get("htmlLink")}


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Tool argument '{key}' must be a non-empty string.")
    return value
