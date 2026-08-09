"""Google Tasks adapter."""

from __future__ import annotations

from typing import Any, Optional, Protocol

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError

from home_assistant_api.errors import UpstreamServiceError

_DEFAULT_TASKLIST = "@default"


class TasksService(Protocol):
    def tasks(self) -> Any:
        ...


def build_tasks_service(credentials: Credentials) -> Resource:
    return build("tasks", "v1", credentials=credentials, cache_discovery=False)


class GoogleTasksClient:
    def __init__(self, service: TasksService) -> None:
        self._service = service

    def list_tasks(self, *, show_completed: bool = False) -> list[dict[str, Any]]:
        try:
            response = (
                self._service.tasks()
                .list(tasklist=_DEFAULT_TASKLIST, showCompleted=show_completed)
                .execute()
            )
        except HttpError as exc:
            raise UpstreamServiceError("Failed to list Google Tasks.") from exc
        return list(response.get("items", []))

    def create_task(self, *, title: str, due_iso: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"title": title}
        if due_iso:
            body["due"] = due_iso
        try:
            return self._service.tasks().insert(tasklist=_DEFAULT_TASKLIST, body=body).execute()
        except HttpError as exc:
            raise UpstreamServiceError("Failed to create a Google Task.") from exc

    def complete_task(self, *, task_id: str) -> dict[str, Any]:
        try:
            return (
                self._service.tasks()
                .patch(tasklist=_DEFAULT_TASKLIST, task=task_id, body={"status": "completed"})
                .execute()
            )
        except HttpError as exc:
            raise UpstreamServiceError("Failed to complete a Google Task.") from exc
