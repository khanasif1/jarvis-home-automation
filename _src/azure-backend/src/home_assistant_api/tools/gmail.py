"""Gmail search tool exposed to the assistant (read-only)."""

from __future__ import annotations

from typing import Any

from home_assistant_api.errors import ConfigurationError, ValidationError
from home_assistant_api.google.gmail_client import GmailClient
from home_assistant_api.tools import ToolContext


def _client_for(context: ToolContext) -> GmailClient:
    if context.credential_store is None:
        raise ConfigurationError("Gmail is not configured for this backend deployment.")
    credentials = context.credential_store.get_credentials(context.device_id)
    service = context.gmail_service_factory(credentials)
    return GmailClient(service)


def search_emails(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    client = _client_for(context)
    query = _require_str(arguments, "query")
    max_results = int(arguments.get("max_results", 5))
    matches = client.search_messages(query=query, max_results=max_results)
    summaries = [
        client.get_message_summary(message_id=match["id"])
        for match in matches
        if "id" in match
    ]
    return {"messages": summaries}


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Tool argument '{key}' must be a non-empty string.")
    return value
