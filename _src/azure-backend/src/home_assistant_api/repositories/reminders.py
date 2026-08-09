"""Reminder repository backing both assistant tools and the Pi poller.

The Pi client's reminder poller (``pi-client/.../reminders/poller.py``) is
expected to call the backend's ``/api/reminders/due`` endpoint on an
interval; this repository stores reminders and exposes exactly the query
that endpoint needs (due, not yet delivered, not cancelled) plus the
acknowledgement write the poller performs after it plays a reminder aloud.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Protocol

from azure.data.tables import TableClient, UpdateMode

from home_assistant_api.errors import NotFoundError
from home_assistant_api.models import Reminder
from home_assistant_api.repositories.table_storage import (
    HttpResponseError,
    ResourceNotFoundError,
    TableBackedRepositoryMixin,
    raise_upstream_error,
)
from home_assistant_api.time_utils import parse_iso8601, to_iso8601, utc_now


class RemindersRepository(Protocol):
    def create(self, device_id: str, title: str, due_at: str) -> Reminder:
        ...

    def list_for_device(self, device_id: str) -> List[Reminder]:
        ...

    def list_due(self, device_id: str, *, as_of: Optional[datetime] = None) -> List[Reminder]:
        ...

    def acknowledge(self, device_id: str, reminder_id: str) -> Reminder:
        ...

    def cancel(self, device_id: str, reminder_id: str) -> Reminder:
        ...


class InMemoryRemindersRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reminders: Dict[str, Reminder] = {}

    def create(self, device_id: str, title: str, due_at: str) -> Reminder:
        # Validate eagerly so a malformed due_at fails at creation time
        # rather than silently never matching the "due" query later.
        parse_iso8601(due_at)
        reminder = Reminder(
            reminder_id=str(uuid.uuid4()),
            device_id=device_id,
            title=title,
            due_at=due_at,
            created_at=to_iso8601(utc_now()),
        )
        with self._lock:
            self._reminders[reminder.reminder_id] = reminder
        return reminder

    def list_for_device(self, device_id: str) -> List[Reminder]:
        with self._lock:
            items = [r for r in self._reminders.values() if r.device_id == device_id]
        return sorted(items, key=lambda r: r.due_at)

    def list_due(self, device_id: str, *, as_of: Optional[datetime] = None) -> List[Reminder]:
        cutoff = as_of or utc_now()
        due: List[Reminder] = []
        for reminder in self.list_for_device(device_id):
            if reminder.cancelled or reminder.delivered:
                continue
            if parse_iso8601(reminder.due_at) <= cutoff:
                due.append(reminder)
        return due

    def acknowledge(self, device_id: str, reminder_id: str) -> Reminder:
        with self._lock:
            reminder = self._require_locked(device_id, reminder_id)
            updated = reminder.model_copy(
                update={"delivered": True, "delivered_at": to_iso8601(utc_now())}
            )
            self._reminders[reminder_id] = updated
            return updated

    def cancel(self, device_id: str, reminder_id: str) -> Reminder:
        with self._lock:
            reminder = self._require_locked(device_id, reminder_id)
            updated = reminder.model_copy(update={"cancelled": True})
            self._reminders[reminder_id] = updated
            return updated

    def _require_locked(self, device_id: str, reminder_id: str) -> Reminder:
        reminder = self._reminders.get(reminder_id)
        if reminder is None or reminder.device_id != device_id:
            raise NotFoundError(f"Reminder '{reminder_id}' was not found for this device.")
        return reminder


def _entity_to_reminder(entity: Mapping[str, Any]) -> Reminder:
    return Reminder(
        reminder_id=str(entity["RowKey"]),
        device_id=str(entity["PartitionKey"]),
        title=str(entity["Title"]),
        due_at=str(entity["DueAt"]),
        created_at=str(entity["CreatedAtUtc"]),
        delivered=bool(entity.get("Delivered", False)),
        delivered_at=entity.get("DeliveredAt"),
        cancelled=bool(entity.get("Cancelled", False)),
    )


def _reminder_to_entity(reminder: Reminder) -> Dict[str, Any]:
    entity: Dict[str, Any] = {
        "PartitionKey": reminder.device_id,
        "RowKey": reminder.reminder_id,
        "Title": reminder.title,
        "DueAt": reminder.due_at,
        "CreatedAtUtc": reminder.created_at,
        "Delivered": reminder.delivered,
        "Cancelled": reminder.cancelled,
    }
    if reminder.delivered_at is not None:
        entity["DeliveredAt"] = reminder.delivered_at
    return entity


class TableRemindersRepository(TableBackedRepositoryMixin):
    """Azure Table Storage implementation backed by the ``Reminders`` table.

    PartitionKey is the owning device id, RowKey is the reminder id, so
    listing (and the "due" scan) a device's reminders is a single-partition
    query.
    """

    def __init__(self, table_client: TableClient) -> None:
        self._table = table_client
        self._table_ensured = False

    def create(self, device_id: str, title: str, due_at: str) -> Reminder:
        self._ensure_table()
        # Validate eagerly, same rationale as InMemoryRemindersRepository.
        parse_iso8601(due_at)
        reminder = Reminder(
            reminder_id=str(uuid.uuid4()),
            device_id=device_id,
            title=title,
            due_at=due_at,
            created_at=to_iso8601(utc_now()),
        )
        try:
            self._table.create_entity(_reminder_to_entity(reminder))
        except HttpResponseError as exc:
            raise_upstream_error("create_reminder", self._table.table_name, exc)
        return reminder

    def list_for_device(self, device_id: str) -> List[Reminder]:
        self._ensure_table()
        try:
            entities = self._table.query_entities(f"PartitionKey eq '{device_id}'")
            items = [_entity_to_reminder(entity) for entity in entities]
        except HttpResponseError as exc:
            raise_upstream_error("list_reminders", self._table.table_name, exc)
            raise  # pragma: no cover - raise_upstream_error always raises
        return sorted(items, key=lambda r: r.due_at)

    def list_due(self, device_id: str, *, as_of: Optional[datetime] = None) -> List[Reminder]:
        cutoff = as_of or utc_now()
        due: List[Reminder] = []
        for reminder in self.list_for_device(device_id):
            if reminder.cancelled or reminder.delivered:
                continue
            if parse_iso8601(reminder.due_at) <= cutoff:
                due.append(reminder)
        return due

    def acknowledge(self, device_id: str, reminder_id: str) -> Reminder:
        return self._update_flags(
            device_id, reminder_id, {"delivered": True, "delivered_at": to_iso8601(utc_now())}
        )

    def cancel(self, device_id: str, reminder_id: str) -> Reminder:
        return self._update_flags(device_id, reminder_id, {"cancelled": True})

    def _update_flags(self, device_id: str, reminder_id: str, update: Dict[str, Any]) -> Reminder:
        self._ensure_table()
        try:
            entity = self._table.get_entity(device_id, reminder_id)
        except ResourceNotFoundError as exc:
            raise NotFoundError(
                f"Reminder '{reminder_id}' was not found for this device."
            ) from exc
        except HttpResponseError as exc:
            raise_upstream_error("get_reminder", self._table.table_name, exc)
        reminder = _entity_to_reminder(entity).model_copy(update=update)
        try:
            self._table.update_entity(_reminder_to_entity(reminder), mode=UpdateMode.REPLACE)
        except HttpResponseError as exc:
            raise_upstream_error("update_reminder", self._table.table_name, exc)
        return reminder
