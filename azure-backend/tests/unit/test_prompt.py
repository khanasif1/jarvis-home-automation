from __future__ import annotations

from datetime import datetime, timezone

import pytest

from home_assistant_api.ai.prompt import build_system_prompt, load_system_prompt_text
from home_assistant_api.errors import ConfigurationError


def test_load_system_prompt_text_reads_real_prompt_file():
    text = load_system_prompt_text()
    assert isinstance(text, str)
    assert len(text) > 0


def test_load_system_prompt_text_honors_env_override(monkeypatch, scratch_dir):
    prompt_path = scratch_dir / "custom_prompt.txt"
    prompt_path.write_text("Custom system prompt content.", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_SYSTEM_PROMPT_PATH", str(prompt_path))
    # Bypass lru_cache from a prior call with a different path by calling the
    # underlying cached function via a fresh path string (cache key differs).
    text = load_system_prompt_text()
    assert text == "Custom system prompt content."


def test_load_system_prompt_text_missing_override_raises(monkeypatch, scratch_dir):
    missing_path = scratch_dir / "does-not-exist.txt"
    monkeypatch.setenv("ASSISTANT_SYSTEM_PROMPT_PATH", str(missing_path))
    with pytest.raises(ConfigurationError):
        load_system_prompt_text()


def test_load_system_prompt_text_empty_override_raises(monkeypatch, scratch_dir):
    empty_path = scratch_dir / "empty_prompt.txt"
    empty_path.write_text("   ", encoding="utf-8")
    monkeypatch.setenv("ASSISTANT_SYSTEM_PROMPT_PATH", str(empty_path))
    with pytest.raises(ConfigurationError):
        load_system_prompt_text()


def test_build_system_prompt_includes_turn_context():
    prompt = build_system_prompt(
        device_id="device-1",
        timezone="America/Los_Angeles",
        now=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        google_configured=True,
    )
    assert "device_id: device-1" in prompt
    assert "device_timezone: America/Los_Angeles" in prompt
    assert "current_utc_time: 2024-01-01T12:00:00Z" in prompt
    assert "google_integration_available: yes" in prompt


def test_build_system_prompt_reports_google_unavailable():
    prompt = build_system_prompt(
        device_id="device-1",
        timezone="UTC",
        now=datetime(2024, 1, 1, tzinfo=timezone.utc),
        google_configured=False,
    )
    assert "google_integration_available: no" in prompt
