"""Reminder tools exposed to the assistant."""

from __future__ import annotations

from typing import Any

from home_assistant_api.errors import ValidationError
from home_assistant_api.time_utils import parse_iso8601
from home_assistant_api.tools import ToolContext


def create_reminder(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    title = _require_str(arguments, "title")
    due_at = _require_str(arguments, "due_at")
    try:
        parse_iso8601(due_at)
    except ValueError as exc:
        raise ValidationError("due_at must be an ISO-8601 timestamp.") from exc
    reminder = context.reminders_repo.create(context.device_id, title, due_at)
    return {"reminder_id": reminder.reminder_id, "title": reminder.title, "due_at": reminder.due_at}


def list_reminders(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    reminders = context.reminders_repo.list_for_device(context.device_id)
    return {
        "reminders": [
            {
                "reminder_id": r.reminder_id,
                "title": r.title,
                "due_at": r.due_at,
                "cancelled": r.cancelled,
                "delivered": r.delivered,
            }
            for r in reminders
        ]
    }


def cancel_reminder(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    reminder_id = _require_str(arguments, "reminder_id")
    reminder = context.reminders_repo.cancel(context.device_id, reminder_id)
    return {"reminder_id": reminder.reminder_id, "cancelled": reminder.cancelled}


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Tool argument '{key}' must be a non-empty string.")
    return value
