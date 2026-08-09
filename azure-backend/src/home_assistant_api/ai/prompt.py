"""Loads and renders the assistant system prompt.

The prompt text lives in ``azure-backend/prompts/assistant_system.txt`` --
a sibling of ``src/`` -- so it ships in the deployment package as a plain
text asset without being embedded in source code, and can be edited without
a code change or dependency bump.
"""

from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from home_assistant_api.errors import ConfigurationError
from home_assistant_api.time_utils import to_iso8601

_PROMPT_ENV_OVERRIDE = "ASSISTANT_SYSTEM_PROMPT_PATH"


def _default_prompt_path() -> Path:
    # .../azure-backend/src/home_assistant_api/ai/prompt.py
    # parents[3] -> .../azure-backend
    return Path(__file__).resolve().parents[3] / "prompts" / "assistant_system.txt"


@lru_cache(maxsize=1)
def _read_prompt_file(path_str: str) -> str:
    path = Path(path_str)
    if not path.is_file():
        raise ConfigurationError(f"Assistant system prompt file not found at '{path}'.")
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        raise ConfigurationError(f"Assistant system prompt file at '{path}' is empty.")
    return content


def load_system_prompt_text() -> str:
    override = os.environ.get(_PROMPT_ENV_OVERRIDE)
    path = Path(override) if override else _default_prompt_path()
    return _read_prompt_file(str(path))


def build_system_prompt(
    *,
    device_id: str,
    timezone: str,
    now: datetime,
    google_configured: bool,
) -> str:
    """Compose the final system message sent to Azure OpenAI for one turn."""

    base = load_system_prompt_text()
    context_lines = [
        "",
        "Current turn context:",
        f"- device_id: {device_id}",
        f"- device_timezone: {timezone}",
        f"- current_utc_time: {to_iso8601(now)}",
        f"- google_integration_available: {'yes' if google_configured else 'no'}",
    ]
    return base + "\n" + "\n".join(context_lines)
