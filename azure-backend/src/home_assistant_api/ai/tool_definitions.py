"""Explicit Azure OpenAI tool (function-calling) definitions.

Every tool the model may call is declared here with a strict JSON schema.
The orchestrator only ever offers this fixed list -- the model cannot invoke
anything the backend has not explicitly defined and implemented in
``ai/tool_executor.py``.
"""

from __future__ import annotations

from typing import Any


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _tool(
        "create_todo",
        "Create a new todo item for this device.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "due_at": {"type": "string", "description": "Optional ISO-8601 due date/time."},
            },
        },
    ),
    _tool(
        "list_todos",
        "List this device's todo items.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "include_done": {"type": "boolean", "default": False},
            },
        },
    ),
    _tool(
        "complete_todo",
        "Mark a todo item as done.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["todo_id"],
            "properties": {"todo_id": {"type": "string"}},
        },
    ),
    _tool(
        "create_reminder",
        "Create a reminder that will be delivered to this device at a future time.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "due_at"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "due_at": {"type": "string", "description": "ISO-8601 date/time, in UTC."},
            },
        },
    ),
    _tool(
        "list_reminders",
        "List this device's reminders.",
        {"type": "object", "additionalProperties": False, "properties": {}},
    ),
    _tool(
        "cancel_reminder",
        "Cancel a previously created reminder.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["reminder_id"],
            "properties": {"reminder_id": {"type": "string"}},
        },
    ),
    _tool(
        "list_calendar_events",
        "List upcoming Google Calendar events. Requires Google Calendar to be connected.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "time_min": {"type": "string", "description": "ISO-8601 lower bound, in UTC."},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            },
        },
    ),
    _tool(
        "create_calendar_event",
        "Create a Google Calendar event. Requires Google Calendar to be connected.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "start", "end"],
            "properties": {
                "summary": {"type": "string", "minLength": 1, "maxLength": 500},
                "start": {"type": "string", "description": "ISO-8601 start date/time."},
                "end": {"type": "string", "description": "ISO-8601 end date/time."},
                "description": {"type": "string", "maxLength": 2000},
            },
        },
    ),
    _tool(
        "list_google_tasks",
        "List Google Tasks. Requires Google Tasks to be connected.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"show_completed": {"type": "boolean", "default": False}},
        },
    ),
    _tool(
        "create_google_task",
        "Create a Google Task. Requires Google Tasks to be connected.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["title"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "due": {"type": "string", "description": "Optional ISO-8601 due date/time."},
            },
        },
    ),
    _tool(
        "complete_google_task",
        "Mark a Google Task complete. Requires Google Tasks to be connected.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
        },
    ),
    _tool(
        "search_emails",
        "Search Gmail read-only for matching messages. Requires Gmail to be connected.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 25, "default": 5},
            },
        },
    ),
]

TOOL_NAMES: frozenset[str] = frozenset(t["function"]["name"] for t in TOOL_DEFINITIONS)
