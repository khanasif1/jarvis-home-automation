"""Strict JSON Schema validation of model-supplied tool call arguments.

``TOOL_DEFINITIONS`` already declares a strict JSON Schema for every tool
(``additionalProperties: False``, required properties, string/number bounds,
etc.), but until now nothing actually validated a model's tool-call
arguments against it -- ``tool_executor.py`` only JSON-decoded them. That
let malformed values reach handlers, sometimes silently miscoerced (for
example ``bool("false")`` is ``True``, so a string ``"false"`` for
``include_done`` behaved like ``True``), extra/unexpected properties pass
straight through to repositories, and out-of-bounds numbers (an
``max_results`` of ``0`` or ``10000``) bypass the limits the schema already
declares.

This module closes that gap: :func:`validate_tool_arguments` re-uses the
exact schema already declared in ``TOOL_DEFINITIONS`` (never a second,
divergent copy) and raises :class:`~home_assistant_api.errors.ValidationError`
-- the same explicit error type every other validation failure in this
backend raises -- the first time ``jsonschema`` finds a violation.
``tool_executor.execute_tool_call`` calls this before dispatching to a
handler, so invalid arguments never reach handler/repository code and are
reported as a structured failed tool result instead of crashing the whole
voice turn.
"""

from __future__ import annotations

from typing import Any

import jsonschema

from home_assistant_api.ai.tool_definitions import TOOL_DEFINITIONS
from home_assistant_api.errors import ValidationError

_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    tool["function"]["name"]: tool["function"]["parameters"] for tool in TOOL_DEFINITIONS
}


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> None:
    """Validate ``arguments`` against the JSON Schema declared for tool ``name``.

    Raises:
        ValidationError: If ``name`` has no registered schema (defensive --
            ``tool_executor.execute_tool_call`` already rejects unknown tool
            names before this is ever reached), or if ``arguments`` violates
            the schema (wrong type, missing required property, unexpected
            additional property, string too long/short, number out of
            bounds, etc). The message and ``details`` name the offending
            JSON pointer path so the failure is actionable without ever
            leaking implementation internals beyond the schema itself.
    """

    schema = _TOOL_SCHEMAS.get(name)
    if schema is None:
        raise ValidationError(f"No argument schema is registered for tool '{name}'.")

    try:
        jsonschema.validate(instance=arguments, schema=schema)
    except jsonschema.exceptions.ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ValidationError(
            f"Tool '{name}' arguments failed validation at '{path}': {exc.message}",
            details={"tool": name, "path": path},
        ) from exc
    except jsonschema.exceptions.SchemaError as exc:  # pragma: no cover - programmer error
        raise ValidationError(
            f"Tool '{name}' has an invalid argument schema; refusing to dispatch."
        ) from exc


__all__ = ["validate_tool_arguments"]
