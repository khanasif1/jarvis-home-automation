"""Dispatches a model tool call to its concrete implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from home_assistant_api.ai.tool_validation import validate_tool_arguments
from home_assistant_api.errors import AppError, ValidationError
from home_assistant_api.tools import ToolContext
from home_assistant_api.tools.gmail import search_emails
from home_assistant_api.tools.google_calendar import create_calendar_event, list_calendar_events
from home_assistant_api.tools.google_tasks import (
    complete_google_task,
    create_google_task,
    list_google_tasks,
)
from home_assistant_api.tools.reminders import cancel_reminder, create_reminder, list_reminders
from home_assistant_api.tools.todos import complete_todo, create_todo, list_todos

ToolFunction = Callable[[ToolContext, dict[str, Any]], dict[str, Any]]

_TOOL_REGISTRY: dict[str, ToolFunction] = {
    "create_todo": create_todo,
    "list_todos": list_todos,
    "complete_todo": complete_todo,
    "create_reminder": create_reminder,
    "list_reminders": list_reminders,
    "cancel_reminder": cancel_reminder,
    "list_calendar_events": list_calendar_events,
    "create_calendar_event": create_calendar_event,
    "list_google_tasks": list_google_tasks,
    "create_google_task": create_google_task,
    "complete_google_task": complete_google_task,
    "search_emails": search_emails,
}


@dataclass(frozen=True)
class ToolCallResult:
    tool_call_id: str
    name: str
    succeeded: bool
    content: dict[str, Any]


def parse_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    if not raw_arguments:
        return {}
    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ValidationError("Tool call arguments were not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("Tool call arguments must be a JSON object.")
    return parsed


def execute_tool_call(
    *,
    context: ToolContext,
    tool_call_id: str,
    name: str,
    raw_arguments: str,
) -> ToolCallResult:
    """Execute one model-requested tool call.

    Unknown tool names raise :class:`ValidationError` -- the executor never
    silently ignores a call it does not recognize; this signals a deeper
    integrity problem (the model was offered a tool this backend does not
    actually implement) and is treated as a turn-level failure rather than
    a per-call one.

    Everything else that can go wrong with *this specific call* -- malformed
    JSON, arguments that violate the tool's declared JSON Schema (wrong
    type, missing required property, unexpected additional property, an
    out-of-bounds number, an overlong string, ...), or an error raised by
    the underlying tool implementation (``ConfigurationError``,
    ``UpstreamServiceError``, ``ValidationError``, ``NotFoundError``) -- is
    caught here and converted into a structured failed result instead of
    failing the whole turn, so the orchestrator can surface a clear action
    outcome (and the model can see exactly what was wrong and retry with
    corrected arguments) for one tool's problem without discarding
    everything else already accomplished in this turn.

    Schema validation runs strictly before ``handler`` is ever invoked --
    an argument set that fails validation never reaches handler/repository
    code, so a mistyped or out-of-range value can never be silently
    miscoerced (for example ``bool("false")`` evaluating to ``True``) or
    passed through untouched.
    """

    handler = _TOOL_REGISTRY.get(name)
    if handler is None:
        raise ValidationError(f"Unknown tool '{name}' was requested by the model.")

    try:
        arguments = parse_tool_arguments(raw_arguments)
        validate_tool_arguments(name, arguments)
        result = handler(context, arguments)
    except AppError as exc:
        return ToolCallResult(
            tool_call_id=tool_call_id,
            name=name,
            succeeded=False,
            content={"error": exc.code, "message": exc.message},
        )
    return ToolCallResult(tool_call_id=tool_call_id, name=name, succeeded=True, content=result)
