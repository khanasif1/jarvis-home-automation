"""Tests for home_assistant_pi.reminders.poller."""

from __future__ import annotations

import time

from home_assistant_pi.api.client import ApiError
from home_assistant_pi.api.models import Reminder
from home_assistant_pi.reminders.poller import ReminderPoller


class FakeApiClient:
    def __init__(self, reminders=None, fetch_error=None, ack_error_ids=None):
        self._reminders = reminders or []
        self._fetch_error = fetch_error
        self._ack_error_ids = set(ack_error_ids or [])
        self.acknowledged = []

    def fetch_due_reminders(self, device_id):
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._reminders

    def acknowledge_reminder(self, reminder_id, device_id):
        if reminder_id in self._ack_error_ids:
            raise ApiError("ack failed")
        self.acknowledged.append(reminder_id)


def make_reminder(rid="r1"):
    return Reminder(id=rid, title=f"reminder {rid}", due_at="2026-01-01T00:00:00Z")


def test_poll_once_delivers_and_acknowledges_when_callback_succeeds():
    api = FakeApiClient(reminders=[make_reminder("r1"), make_reminder("r2")])
    delivered = []

    def on_reminder(reminder):
        delivered.append(reminder)
        return True

    poller = ReminderPoller(api, "pi-1", on_reminder=on_reminder)
    result = poller.poll_once()

    assert [r.id for r in result] == ["r1", "r2"]
    assert delivered == result
    assert api.acknowledged == ["r1", "r2"]


def test_poll_once_does_not_acknowledge_deferred_reminder():
    """Regression test: a reminder the callback defers (busy assistant,
    returns False) must never be acknowledged -- it needs to remain due so
    the next poll retries it instead of losing it."""
    api = FakeApiClient(reminders=[make_reminder("r1")])
    delivered = []

    def on_reminder(reminder):
        delivered.append(reminder)
        return False  # simulates Application.handle_reminder deferring

    poller = ReminderPoller(api, "pi-1", on_reminder=on_reminder)
    result = poller.poll_once()

    assert len(delivered) == 1  # callback was invoked
    assert result == []  # not reported as delivered
    assert api.acknowledged == []  # and crucially, never acknowledged


def test_poll_once_does_not_acknowledge_when_callback_returns_none():
    """A callback that returns None (the old, unsafe default of just doing
    work without a return value) must be treated as "not delivered", not
    accidentally acknowledged."""
    api = FakeApiClient(reminders=[make_reminder("r1")])
    poller = ReminderPoller(api, "pi-1", on_reminder=lambda r: None)

    result = poller.poll_once()

    assert result == []
    assert api.acknowledged == []


def test_poll_once_handles_fetch_error_gracefully():
    api = FakeApiClient(fetch_error=ApiError("network down"))
    delivered = []
    poller = ReminderPoller(
        api, "pi-1", on_reminder=lambda r: (delivered.append(r), True)[1]
    )

    result = poller.poll_once()

    assert result == []
    assert delivered == []


def test_poll_once_handles_acknowledge_error_but_still_invokes_callback():
    api = FakeApiClient(reminders=[make_reminder("r1")], ack_error_ids={"r1"})
    delivered = []
    poller = ReminderPoller(
        api, "pi-1", on_reminder=lambda r: (delivered.append(r), True)[1]
    )

    result = poller.poll_once()

    # Callback was invoked (and returned True: delivered) even though
    # acknowledgement failed...
    assert len(delivered) == 1
    # ...but the reminder is not reported as "delivered" since ack failed.
    assert result == []


def test_poll_once_handles_callback_exception():
    api = FakeApiClient(reminders=[make_reminder("r1")])

    def bad_callback(reminder):
        raise RuntimeError("speaker exploded")

    poller = ReminderPoller(api, "pi-1", on_reminder=bad_callback)
    result = poller.poll_once()
    assert result == []
    assert api.acknowledged == []  # never reached due to callback exception


def test_start_and_stop_background_thread():
    api = FakeApiClient(reminders=[])
    poller = ReminderPoller(
        api, "pi-1", on_reminder=lambda r: True, poll_interval_seconds=0.05
    )
    poller.start()
    time.sleep(0.2)
    poller.stop(timeout=2)
    assert poller._thread is None


def test_start_is_idempotent_when_already_running():
    api = FakeApiClient(reminders=[])
    poller = ReminderPoller(
        api, "pi-1", on_reminder=lambda r: True, poll_interval_seconds=1
    )
    poller.start()
    first_thread = poller._thread
    poller.start()  # should not spawn a second thread
    assert poller._thread is first_thread
    poller.stop(timeout=2)
