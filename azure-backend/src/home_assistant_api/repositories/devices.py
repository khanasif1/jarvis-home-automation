"""Device registry repository."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol

from azure.data.tables import TableClient, UpdateMode

from home_assistant_api.errors import ConflictError, NotFoundError
from home_assistant_api.repositories.table_storage import (
    DEVICE_PARTITION_KEY,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    TableBackedRepositoryMixin,
    raise_upstream_error,
)
from home_assistant_api.time_utils import to_iso8601, utc_now


@dataclass(frozen=True)
class DeviceRecord:
    device_id: str
    display_name: str
    token_hash: str
    registered_at: str
    last_seen_at: Optional[str] = None
    # Matches the ``Enabled`` field infra/scripts/provision-device.* writes.
    # A device provisioned (or later disabled) out of band can be blocked
    # from authenticating without deleting its record.
    enabled: bool = True


class DevicesRepository(Protocol):
    def register(self, device_id: str, display_name: str, token_hash: str) -> DeviceRecord:
        """Register a new device. Raises ConflictError if it already exists."""

    def upsert_token(self, device_id: str, display_name: str, token_hash: str) -> DeviceRecord:
        """Register a device or rotate its token if it already exists."""

    def get(self, device_id: str) -> Optional[DeviceRecord]:
        ...

    def require(self, device_id: str) -> DeviceRecord:
        """Return the device or raise NotFoundError."""

    def list_all(self) -> List[DeviceRecord]:
        ...

    def touch_last_seen(self, device_id: str) -> None:
        ...

    def set_enabled(self, device_id: str, enabled: bool) -> DeviceRecord:
        """Enable or disable a device without deleting its record. Raises NotFoundError."""


class InMemoryDevicesRepository:
    """Thread-safe, process-local implementation of :class:`DevicesRepository`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._devices: Dict[str, DeviceRecord] = {}

    def register(self, device_id: str, display_name: str, token_hash: str) -> DeviceRecord:
        with self._lock:
            if device_id in self._devices:
                raise ConflictError(f"Device '{device_id}' is already registered.")
            record = DeviceRecord(
                device_id=device_id,
                display_name=display_name,
                token_hash=token_hash,
                registered_at=to_iso8601(utc_now()),
            )
            self._devices[device_id] = record
            return record

    def upsert_token(self, device_id: str, display_name: str, token_hash: str) -> DeviceRecord:
        with self._lock:
            existing = self._devices.get(device_id)
            record = DeviceRecord(
                device_id=device_id,
                display_name=display_name,
                token_hash=token_hash,
                registered_at=existing.registered_at if existing else to_iso8601(utc_now()),
                last_seen_at=existing.last_seen_at if existing else None,
                enabled=existing.enabled if existing else True,
            )
            self._devices[device_id] = record
            return record

    def get(self, device_id: str) -> Optional[DeviceRecord]:
        with self._lock:
            return self._devices.get(device_id)

    def require(self, device_id: str) -> DeviceRecord:
        record = self.get(device_id)
        if record is None:
            raise NotFoundError(f"Device '{device_id}' is not registered.")
        return record

    def list_all(self) -> List[DeviceRecord]:
        with self._lock:
            return list(self._devices.values())

    def touch_last_seen(self, device_id: str) -> None:
        with self._lock:
            existing = self._devices.get(device_id)
            if existing is None:
                raise NotFoundError(f"Device '{device_id}' is not registered.")
            self._devices[device_id] = DeviceRecord(
                device_id=existing.device_id,
                display_name=existing.display_name,
                token_hash=existing.token_hash,
                registered_at=existing.registered_at,
                last_seen_at=to_iso8601(utc_now()),
                enabled=existing.enabled,
            )

    def set_enabled(self, device_id: str, enabled: bool) -> DeviceRecord:
        with self._lock:
            existing = self._devices.get(device_id)
            if existing is None:
                raise NotFoundError(f"Device '{device_id}' is not registered.")
            updated = DeviceRecord(
                device_id=existing.device_id,
                display_name=existing.display_name,
                token_hash=existing.token_hash,
                registered_at=existing.registered_at,
                last_seen_at=existing.last_seen_at,
                enabled=enabled,
            )
            self._devices[device_id] = updated
            return updated


def _entity_to_device_record(entity: Mapping[str, Any]) -> DeviceRecord:
    return DeviceRecord(
        device_id=str(entity["RowKey"]),
        display_name=str(entity["DeviceName"]),
        token_hash=str(entity["TokenHash"]),
        registered_at=str(entity["CreatedAtUtc"]),
        last_seen_at=entity.get("LastSeenAtUtc"),
        enabled=bool(entity.get("Enabled", True)),
    )


def _device_record_to_entity(record: DeviceRecord) -> Dict[str, Any]:
    entity: Dict[str, Any] = {
        "PartitionKey": DEVICE_PARTITION_KEY,
        "RowKey": record.device_id,
        "DeviceName": record.display_name,
        "TokenHash": record.token_hash,
        "Enabled": record.enabled,
        "CreatedAtUtc": record.registered_at,
    }
    if record.last_seen_at is not None:
        entity["LastSeenAtUtc"] = record.last_seen_at
    return entity


class TableDevicesRepository(TableBackedRepositoryMixin):
    """Azure Table Storage implementation backed by the ``Devices`` table.

    Reads and writes the *exact* entity shape
    ``infra/scripts/provision-device.*`` produces: PartitionKey ``"device"``,
    RowKey = device UUID, and ``DeviceName``/``TokenHash``/``Enabled``/
    ``CreatedAtUtc``/``LastSeenAtUtc`` properties. A device provisioned by
    that script is therefore immediately usable by this backend without any
    migration step.
    """

    def __init__(self, table_client: TableClient) -> None:
        self._table = table_client
        self._table_ensured = False

    def register(self, device_id: str, display_name: str, token_hash: str) -> DeviceRecord:
        self._ensure_table()
        record = DeviceRecord(
            device_id=device_id,
            display_name=display_name,
            token_hash=token_hash,
            registered_at=to_iso8601(utc_now()),
        )
        try:
            self._table.create_entity(_device_record_to_entity(record))
        except ResourceExistsError as exc:
            raise ConflictError(f"Device '{device_id}' is already registered.") from exc
        except HttpResponseError as exc:
            raise_upstream_error("register_device", self._table.table_name, exc)
        return record

    def upsert_token(self, device_id: str, display_name: str, token_hash: str) -> DeviceRecord:
        self._ensure_table()
        existing = self.get(device_id)
        record = DeviceRecord(
            device_id=device_id,
            display_name=display_name,
            token_hash=token_hash,
            registered_at=existing.registered_at if existing else to_iso8601(utc_now()),
            last_seen_at=existing.last_seen_at if existing else None,
            enabled=existing.enabled if existing else True,
        )
        try:
            self._table.upsert_entity(_device_record_to_entity(record), mode=UpdateMode.REPLACE)
        except HttpResponseError as exc:
            raise_upstream_error("upsert_token", self._table.table_name, exc)
        return record

    def get(self, device_id: str) -> Optional[DeviceRecord]:
        self._ensure_table()
        try:
            entity = self._table.get_entity(DEVICE_PARTITION_KEY, device_id)
        except ResourceNotFoundError:
            return None
        except HttpResponseError as exc:
            raise_upstream_error("get_device", self._table.table_name, exc)
        return _entity_to_device_record(entity)

    def require(self, device_id: str) -> DeviceRecord:
        record = self.get(device_id)
        if record is None:
            raise NotFoundError(f"Device '{device_id}' is not registered.")
        return record

    def list_all(self) -> List[DeviceRecord]:
        self._ensure_table()
        try:
            entities = self._table.query_entities(
                f"PartitionKey eq '{DEVICE_PARTITION_KEY}'"
            )
            return [_entity_to_device_record(entity) for entity in entities]
        except HttpResponseError as exc:
            raise_upstream_error("list_devices", self._table.table_name, exc)
            raise  # pragma: no cover - raise_upstream_error always raises

    def touch_last_seen(self, device_id: str) -> None:
        self._ensure_table()
        try:
            self._table.update_entity(
                {
                    "PartitionKey": DEVICE_PARTITION_KEY,
                    "RowKey": device_id,
                    "LastSeenAtUtc": to_iso8601(utc_now()),
                },
                mode=UpdateMode.MERGE,
            )
        except ResourceNotFoundError as exc:
            raise NotFoundError(f"Device '{device_id}' is not registered.") from exc
        except HttpResponseError as exc:
            raise_upstream_error("touch_last_seen", self._table.table_name, exc)

    def set_enabled(self, device_id: str, enabled: bool) -> DeviceRecord:
        self._ensure_table()
        try:
            self._table.update_entity(
                {
                    "PartitionKey": DEVICE_PARTITION_KEY,
                    "RowKey": device_id,
                    "Enabled": enabled,
                },
                mode=UpdateMode.MERGE,
            )
        except ResourceNotFoundError as exc:
            raise NotFoundError(f"Device '{device_id}' is not registered.") from exc
        except HttpResponseError as exc:
            raise_upstream_error("set_enabled", self._table.table_name, exc)
        return self.require(device_id)
