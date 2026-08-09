"""Todo-list tools exposed to the assistant."""

from __future__ import annotations

from typing import Any

from home_assistant_api.errors import ValidationError
from home_assistant_api.tools import ToolContext


def create_todo(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    title = _require_str(arguments, "title")
    due_at = arguments.get("due_at")
    todo = context.todos_repo.create(context.device_id, title, due_at)
    return {"todo_id": todo.todo_id, "title": todo.title, "due_at": todo.due_at}


def list_todos(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    include_done = bool(arguments.get("include_done", False))
    todos = context.todos_repo.list_for_device(context.device_id, include_done=include_done)
    return {
        "todos": [
            {"todo_id": t.todo_id, "title": t.title, "done": t.done, "due_at": t.due_at}
            for t in todos
        ]
    }


def complete_todo(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    todo_id = _require_str(arguments, "todo_id")
    todo = context.todos_repo.complete(context.device_id, todo_id)
    return {"todo_id": todo.todo_id, "done": todo.done}


def _require_str(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Tool argument '{key}' must be a non-empty string.")
    return value
