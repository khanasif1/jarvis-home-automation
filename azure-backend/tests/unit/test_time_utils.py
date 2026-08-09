from __future__ import annotations

from datetime import datetime, timezone

import pytest

from home_assistant_api.time_utils import Stopwatch, parse_iso8601, to_iso8601, utc_now


def test_utc_now_is_timezone_aware():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0


def test_to_iso8601_uses_z_suffix():
    dt = datetime(2024, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
    assert to_iso8601(dt) == "2024-01-01T12:30:00Z"


def test_to_iso8601_normalizes_naive_datetime_to_utc():
    dt = datetime(2024, 1, 1, 12, 30, 0)
    assert to_iso8601(dt) == "2024-01-01T12:30:00Z"


def test_parse_iso8601_handles_z_suffix():
    parsed = parse_iso8601("2024-01-01T12:30:00Z")
    assert parsed.tzinfo is not None
    assert parsed.hour == 12


def test_parse_iso8601_roundtrip():
    original = utc_now().replace(microsecond=0)
    assert parse_iso8601(to_iso8601(original)) == original


def test_parse_iso8601_rejects_garbage():
    with pytest.raises(ValueError):
        parse_iso8601("not-a-date")


def test_stopwatch_measures_elapsed_time():
    with Stopwatch() as sw:
        pass
    assert sw.elapsed_ms >= 0


def test_stopwatch_elapsed_ms_stable_after_exit():
    with Stopwatch() as sw:
        pass
    first = sw.elapsed_ms
    second = sw.elapsed_ms
    assert first == second
