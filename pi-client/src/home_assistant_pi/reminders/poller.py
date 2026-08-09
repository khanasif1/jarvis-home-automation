"""Background poller that checks the backend for due reminders.

The poller is deliberately simple and synchronous: it is intended to be run
from a dedicated thread (see ``main.py``) that calls :meth:`poll_once` in a
loop, or to be driven directly by tests via :meth:`poll_once` without any
threading involved.

``on_reminder`` must return ``True`` when the reminder was actually
delivered to the user and ``False`` (or ``None``) when delivery was
deferred (e.g. the assistant was mid-conversation) or failed. Only
reminders whose callback returns a truthy value are acknowledged to the
backend -- a deferred reminder must remain due so it is retried on the next
poll instead of silently disappearing.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from ..api.client import ApiClient, ApiError
from ..api.models import Reminder

logger = logging.getLogger(__name__)


class ReminderPoller:
    """Polls the backend for due reminders and hands them to a callback."""

    def __init__(
        self,
        api_client: ApiClient,
        device_id: str,
        on_reminder: Callable[[Reminder], bool],
        poll_interval_seconds: float = 60.0,
    ) -> None:
        self.api_client = api_client
        self.device_id = device_id
        self.on_reminder = on_reminder
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def poll_once(self) -> list[Reminder]:
        """Fetch due reminders once, invoke the callback, and acknowledge
        only the reminders that were actually delivered.

        Returns the list of reminders that were delivered *and*
        successfully acknowledged. Network/API errors are logged and
        swallowed so a transient backend outage does not crash the
        assistant; the next poll will simply try again. A reminder that the
        callback defers (busy assistant) or that raises is never
        acknowledged, so it remains due and is retried on the next poll.
        """
        try:
            reminders = self.api_client.fetch_due_reminders(self.device_id)
        except ApiError as exc:
            logger.warning("Failed to fetch due reminders: %s", exc)
            return []

        delivered = []
        for reminder in reminders:
            try:
                was_delivered = bool(self.on_reminder(reminder))
            except Exception:
                logger.exception(
                    "Reminder callback raised for reminder %s", reminder.id
                )
                continue

            if not was_delivered:
                logger.info(
                    "Reminder %s not acknowledged (deferred or not delivered)",
                    reminder.id,
                )
                continue

            try:
                self.api_client.acknowledge_reminder(reminder.id, self.device_id)
                delivered.append(reminder)
            except ApiError as exc:
                logger.warning(
                    "Failed to acknowledge reminder %s: %s", reminder.id, exc
                )
        return delivered

    def start(self) -> None:
        """Start polling on a background daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="reminder-poller", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        """Signal the polling thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.poll_interval_seconds)
