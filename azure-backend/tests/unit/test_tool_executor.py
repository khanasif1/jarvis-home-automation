from __future__ import annotations

import json

import pytest

from home_assistant_api.ai.tool_executor import execute_tool_call, parse_tool_arguments
from home_assistant_api.errors import ValidationError
from home_assistant_api.repositories.reminders import InMemoryRemindersRepository
from home_assistant_api.repositories.todos import InMemoryTodosRepository
from home_assistant_api.tools import ToolContext


def _context() -> ToolContext:
    return ToolContext(
        device_id="device-1",
        todos_repo=InMemoryTodosRepository(),
        reminders_repo=InMemoryRemindersRepository(),
        credential_store=None,
    )


def test_parse_tool_arguments_empty_string_returns_empty_dict():
    assert parse_tool_arguments("") == {}


def test_parse_tool_arguments_parses_valid_json():
    assert parse_tool_arguments('{"title": "milk"}') == {"title": "milk"}


def test_parse_tool_arguments_invalid_json_raises():
    with pytest.raises(ValidationError):
        parse_tool_arguments("not-json")


def test_parse_tool_arguments_non_object_raises():
    with pytest.raises(ValidationError):
        parse_tool_arguments("[1, 2, 3]")


def test_execute_tool_call_unknown_tool_raises():
    with pytest.raises(ValidationError):
        execute_tool_call(
            context=_context(),
            tool_call_id="call-1",
            name="not_a_real_tool",
            raw_arguments="{}",
        )


def test_execute_tool_call_success_returns_result():
    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="create_todo",
        raw_arguments='{"title": "Buy milk"}',
    )
    assert result.succeeded is True
    assert result.name == "create_todo"
    assert result.content["title"] == "Buy milk"


def test_execute_tool_call_app_error_converts_to_failed_result():
    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="complete_todo",
        raw_arguments='{"todo_id": "missing"}',
    )
    assert result.succeeded is False
    assert result.content["error"] == "not_found"


def test_execute_tool_call_google_tool_without_credential_store_fails_explicitly():
    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="list_calendar_events",
        raw_arguments="{}",
    )
    assert result.succeeded is False
    assert result.content["error"] == "configuration_error"


def test_execute_tool_call_invalid_arguments_converts_to_failed_result():
    """Malformed JSON in a tool call's arguments must not fail the whole
    turn -- it is a per-call problem, converted to a structured failed
    result exactly like any other tool-level ``AppError``."""

    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="create_todo",
        raw_arguments="not-json",
    )
    assert result.succeeded is False
    assert result.content["error"] == "invalid_request"


def test_execute_tool_call_string_instead_of_boolean_fails_validation_not_miscoerced():
    """Regression test for the exact bug this validation closes:
    ``bool("false")`` is ``True`` in Python, so before schema validation
    was enforced, a string ``"false"`` for ``include_done`` behaved as if
    it were ``True``. It must now be rejected before the handler ever
    sees it."""

    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="list_todos",
        raw_arguments='{"include_done": "false"}',
    )
    assert result.succeeded is False
    assert result.content["error"] == "invalid_request"


def test_execute_tool_call_unexpected_additional_property_fails_validation():
    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="create_todo",
        raw_arguments='{"title": "Buy milk", "unexpected_field": "x"}',
    )
    assert result.succeeded is False
    assert result.content["error"] == "invalid_request"


def test_execute_tool_call_overlong_string_fails_validation():
    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="create_todo",
        raw_arguments=json.dumps({"title": "x" * 501}),
    )
    assert result.succeeded is False
    assert result.content["error"] == "invalid_request"


def test_execute_tool_call_out_of_bounds_number_fails_validation():
    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="list_calendar_events",
        raw_arguments='{"max_results": 500}',
    )
    assert result.succeeded is False
    assert result.content["error"] == "invalid_request"


def test_execute_tool_call_missing_required_property_fails_validation():
    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="create_reminder",
        raw_arguments="{}",
    )
    assert result.succeeded is False
    assert result.content["error"] == "invalid_request"


def test_execute_tool_call_valid_boolean_include_done_is_accepted():
    result = execute_tool_call(
        context=_context(),
        tool_call_id="call-1",
        name="list_todos",
        raw_arguments='{"include_done": true}',
    )
    assert result.succeeded is True
