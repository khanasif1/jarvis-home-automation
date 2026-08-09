"""Gmail adapter.

Only read-only search is implemented, matching the recommended
``gmail.readonly`` scope documented in ``docs/google-oauth-setup.md``. Send
and delete capabilities are intentionally out of scope until a reviewed
feature explicitly requires them.
"""

from __future__ import annotations

from typing import Any, Protocol

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from home_assistant_api.errors import UpstreamServiceError


class GmailService(Protocol):
    def users(self) -> Any:
        ...


def build_gmail_service(credentials: Credentials) -> Resource:
    return build("gmail", "v1", credentials=credentials, cache_discovery=False)


class GmailClient:
    def __init__(self, service: GmailService) -> None:
        self._service = service

    def search_messages(self, *, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        try:
            response = (
                self._service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
        except HttpError as exc:
            raise UpstreamServiceError("Failed to search Gmail messages.") from exc
        return list(response.get("messages", []))

    def get_message_summary(self, *, message_id: str) -> dict[str, Any]:
        try:
            message = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="metadata")
                .execute()
            )
        except HttpError as exc:
            raise UpstreamServiceError("Failed to fetch a Gmail message.") from exc
        headers = {
            header["name"]: header["value"]
            for header in message.get("payload", {}).get("headers", [])
        }
        return {
            "id": message.get("id"),
            "snippet": message.get("snippet"),
            "subject": headers.get("Subject"),
            "from": headers.get("From"),
        }
