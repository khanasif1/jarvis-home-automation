"""Google Tasks tools exposed to the assistant."""

from __future__ import annotations

from typing import Any

from home_assistant_api.errors import ConfigurationError, ValidationError
from home_assistant_api.google.tasks_client import GoogleTasksClient
from home_assistant_api.tools import ToolContext


def _client_for(context: ToolContext) -> GoogleTasksClient:
    if context.credential_store is None:
        raise ConfigurationError("Google Tasks is not configured for this backend deployment.")
    credentials = context.credential_store.get_credentials(context.device_id)
    service = context.tasks_service_factory(credentials)
    return GoogleTasksClient(service)


def list_google_tasks(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    client = _client_for(context)
    show_completed = bool(arguments.get("show_completed", False))
    tasks = client.list_tasks(show_completed=show_completed)
    return {
        "tasks": [
            {"id": t.get("id"), "title": t.get("title"), "status": t.get("status")}
            for t in tasks
        ]
    }


def create_google_task(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    client = _client_for(context)
    title = _require_str(arguments, "title")
    due_iso = arguments.get("due")
    task = client.create_task(title=title, due_iso=due_iso)
    return {"id": task.get("id"), "title": task.get("title")}


def complete_google_task(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    client = _client_for(context)
    task_id = _require_str(arguments, "task_id")
    task = client.complete_task(task_id=task_id)
    return {"id": task.get("id"), "status": task.get("status")}


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Tool argument '{key}' must be a non-empty string.")
    return value
