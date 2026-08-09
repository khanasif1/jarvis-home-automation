"""Unit tests for :mod:`home_assistant_api.ai.tool_validation`.

Proves strict JSON Schema enforcement of tool-call arguments -- the exact
schema already declared in ``TOOL_DEFINITIONS``, not a second copy -- runs
before dispatch: wrong types, unexpected additional properties, string
length bounds, and numeric bounds are all rejected with
:class:`~home_assistant_api.errors.ValidationError`; valid/omitted-optional
arguments are accepted silently (returns ``None``, raises nothing).
"""

from __future__ import annotations

import pytest

from home_assistant_api.ai.tool_validation import validate_tool_arguments
from home_assistant_api.errors import ValidationError


def test_valid_arguments_pass_silently():
    validate_tool_arguments("create_todo", {"title": "Buy milk"})


def test_valid_arguments_with_all_optional_properties_pass():
    validate_tool_arguments(
        "create_todo", {"title": "Buy milk", "due_at": "2026-01-01T00:00:00Z"}
    )


def test_missing_required_property_fails():
    with pytest.raises(ValidationError):
        validate_tool_arguments("create_todo", {})


def test_missing_one_of_two_required_properties_fails():
    with pytest.raises(ValidationError):
        validate_tool_arguments("create_reminder", {"title": "Take out trash"})


def test_wrong_type_string_expected_fails():
    with pytest.raises(ValidationError):
        validate_tool_arguments("create_todo", {"title": 12345})


def test_wrong_type_boolean_expected_string_supplied_fails():
    """The concrete bug this validation closes: a JSON string ``"false"``
    for a boolean property must be rejected, not silently miscoerced by
    Python's ``bool("false") == True``."""

    with pytest.raises(ValidationError):
        validate_tool_arguments("list_todos", {"include_done": "false"})


def test_wrong_type_boolean_expected_string_true_also_fails():
    with pytest.raises(ValidationError):
        validate_tool_arguments("list_todos", {"include_done": "true"})


def test_correct_boolean_type_passes():
    validate_tool_arguments("list_todos", {"include_done": True})
    validate_tool_arguments("list_todos", {"include_done": False})


def test_omitted_optional_boolean_passes():
    validate_tool_arguments("list_todos", {})


def test_additional_property_rejected():
    with pytest.raises(ValidationError):
        validate_tool_arguments("create_todo", {"title": "Buy milk", "priority": "high"})


def test_string_exceeding_max_length_rejected():
    with pytest.raises(ValidationError):
        validate_tool_arguments("create_todo", {"title": "x" * 501})


def test_string_at_max_length_boundary_passes():
    validate_tool_arguments("create_todo", {"title": "x" * 500})


def test_empty_required_string_rejected():
    with pytest.raises(ValidationError):
        validate_tool_arguments("create_todo", {"title": ""})


def test_numeric_below_minimum_rejected():
    with pytest.raises(ValidationError):
        validate_tool_arguments("list_calendar_events", {"max_results": 0})


def test_numeric_above_maximum_rejected():
    with pytest.raises(ValidationError):
        validate_tool_arguments("list_calendar_events", {"max_results": 51})


def test_numeric_at_bounds_passes():
    validate_tool_arguments("list_calendar_events", {"max_results": 1})
    validate_tool_arguments("list_calendar_events", {"max_results": 50})


def test_numeric_wrong_type_rejected():
    with pytest.raises(ValidationError):
        validate_tool_arguments("list_calendar_events", {"max_results": "10"})


def test_empty_object_valid_for_no_argument_tool():
    validate_tool_arguments("list_reminders", {})


def test_no_argument_tool_rejects_any_property():
    with pytest.raises(ValidationError):
        validate_tool_arguments("list_reminders", {"unexpected": True})


def test_unregistered_tool_name_raises():
    with pytest.raises(ValidationError):
        validate_tool_arguments("not_a_real_tool", {})


def test_error_details_name_offending_path():
    with pytest.raises(ValidationError) as excinfo:
        validate_tool_arguments("create_todo", {"title": "Buy milk", "extra": 1})
    assert excinfo.value.details is not None
    assert excinfo.value.details["tool"] == "create_todo"
