"""Per-device Google credential storage and refresh.

Stores exactly the fields needed to reconstruct a
``google.oauth2.credentials.Credentials`` object, keyed by device id. The
storage *backend* (in-memory or Azure Table Storage) is pluggable via
:class:`CredentialStorageBackend`; :class:`CredentialStore` itself only
implements the refresh-on-read logic shared by every backend. Refresh
tokens never leave the process; the Pi client never receives them (see
``docs/security.md``), and this module never logs a token value.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Mapping, Optional, Protocol

from azure.data.tables import TableClient, UpdateMode
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from home_assistant_api.errors import ConfigurationError, UpstreamServiceError
from home_assistant_api.google.oauth import StoredCredentialData
from home_assistant_api.repositories.table_storage import (
    HttpResponseError,
    ResourceNotFoundError,
    TableBackedRepositoryMixin,
    raise_upstream_error,
)
from home_assistant_api.time_utils import parse_iso8601

# All Google credential entities share this partition; RowKey is the device
# id. There is only ever a handful of devices per household, so this is a
# small, single-partition table -- storage-at-rest encryption (the Azure
# Storage account default) is an acceptable protection for these secrets,
# but they are never written to logs or telemetry regardless.
_CREDENTIALS_PARTITION_KEY = "google_credential"


class CredentialStorageBackend(Protocol):
    """Pluggable persistence for :class:`StoredCredentialData`."""

    def save(self, device_id: str, data: StoredCredentialData) -> None:
        ...

    def delete(self, device_id: str) -> None:
        ...

    def get(self, device_id: str) -> Optional[StoredCredentialData]:
        ...


class InMemoryCredentialStorage:
    """Thread-safe, process-local credential storage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, StoredCredentialData] = {}

    def save(self, device_id: str, data: StoredCredentialData) -> None:
        with self._lock:
            self._records[device_id] = data

    def delete(self, device_id: str) -> None:
        with self._lock:
            self._records.pop(device_id, None)

    def get(self, device_id: str) -> Optional[StoredCredentialData]:
        with self._lock:
            return self._records.get(device_id)


def _entity_to_credential_data(entity: Mapping[str, Any]) -> StoredCredentialData:
    return StoredCredentialData(
        token=str(entity["Token"]),
        refresh_token=entity.get("RefreshToken"),
        token_uri=str(entity["TokenUri"]),
        client_id=str(entity["ClientId"]),
        client_secret=str(entity["ClientSecret"]),
        scopes=tuple(json.loads(str(entity.get("ScopesJson") or "[]"))),
        expiry_iso=entity.get("ExpiryIso"),
    )


def _credential_data_to_entity(device_id: str, data: StoredCredentialData) -> Dict[str, Any]:
    entity: Dict[str, Any] = {
        "PartitionKey": _CREDENTIALS_PARTITION_KEY,
        "RowKey": device_id,
        "Token": data.token,
        "TokenUri": data.token_uri,
        "ClientId": data.client_id,
        "ClientSecret": data.client_secret,
        "ScopesJson": json.dumps(list(data.scopes)),
    }
    if data.refresh_token is not None:
        entity["RefreshToken"] = data.refresh_token
    if data.expiry_iso is not None:
        entity["ExpiryIso"] = data.expiry_iso
    return entity


class TableCredentialStorage(TableBackedRepositoryMixin):
    """Azure Table Storage implementation backed by the ``GoogleCredentials`` table.

    ``GoogleCredentials`` is IaC-provisioned by
    ``infra/modules/storage.bicep`` alongside
    ``Todos``/``Reminders``/``Sessions``/``Devices``/``Idempotency`` --
    production relies on that provisioning, not on this backend creating
    the table. The idempotent create-on-first-use in :meth:`_ensure_table`
    (see :func:`ensure_table_exists`) is a harmless no-op against an
    already-provisioned table (the ``Storage Table Data Contributor`` role
    already granted to the Function App's managed identity includes the
    table-management data actions it would need, so it never risks
    breaking managed-identity auth); it remains useful for local/Azurite
    development, where nothing pre-provisions tables.

    Storage encryption-at-rest protects credential fields; the token,
    refresh token, and client secret are never logged.
    """

    def __init__(self, table_client: TableClient) -> None:
        self._table = table_client
        self._table_ensured = False

    def save(self, device_id: str, data: StoredCredentialData) -> None:
        self._ensure_table()
        try:
            self._table.upsert_entity(
                _credential_data_to_entity(device_id, data), mode=UpdateMode.REPLACE
            )
        except HttpResponseError as exc:
            raise_upstream_error("save_google_credential", self._table.table_name, exc)

    def delete(self, device_id: str) -> None:
        self._ensure_table()
        try:
            self._table.delete_entity(_CREDENTIALS_PARTITION_KEY, device_id)
        except ResourceNotFoundError:
            return
        except HttpResponseError as exc:
            raise_upstream_error("delete_google_credential", self._table.table_name, exc)

    def get(self, device_id: str) -> Optional[StoredCredentialData]:
        self._ensure_table()
        try:
            entity = self._table.get_entity(_CREDENTIALS_PARTITION_KEY, device_id)
        except ResourceNotFoundError:
            return None
        except HttpResponseError as exc:
            raise_upstream_error("get_google_credential", self._table.table_name, exc)
            raise  # pragma: no cover - raise_upstream_error always raises
        return _entity_to_credential_data(entity)


class CredentialStore:
    """Refresh-on-read logic shared by every :class:`CredentialStorageBackend`."""

    def __init__(self, storage: Optional[CredentialStorageBackend] = None) -> None:
        self._storage: CredentialStorageBackend = storage or InMemoryCredentialStorage()

    def save(self, device_id: str, data: StoredCredentialData) -> None:
        self._storage.save(device_id, data)

    def delete(self, device_id: str) -> None:
        self._storage.delete(device_id)

    def _require_record(self, device_id: str) -> StoredCredentialData:
        record = self._storage.get(device_id)
        if record is None:
            raise ConfigurationError(
                f"Google integration is not connected for device '{device_id}'. "
                "Complete the Google authorization flow first."
            )
        return record

    def get_credentials(self, device_id: str) -> Credentials:
        """Return valid, refreshed Google credentials for ``device_id``.

        Raises:
            ConfigurationError: If the device never completed Google OAuth.
            UpstreamServiceError: If a required token refresh fails.
        """

        record = self._require_record(device_id)
        expiry = parse_iso8601(record.expiry_iso).replace(tzinfo=None) if record.expiry_iso else None
        credentials = Credentials(
            token=record.token,
            refresh_token=record.refresh_token,
            token_uri=record.token_uri,
            client_id=record.client_id,
            client_secret=record.client_secret,
            scopes=list(record.scopes),
            expiry=expiry,
        )
        if credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                raise UpstreamServiceError(
                    f"Failed to refresh Google credentials for device '{device_id}'."
                ) from exc
            self.save(
                device_id,
                StoredCredentialData(
                    token=credentials.token,
                    refresh_token=credentials.refresh_token,
                    token_uri=credentials.token_uri,
                    client_id=credentials.client_id,
                    client_secret=credentials.client_secret,
                    scopes=tuple(credentials.scopes or []),
                    expiry_iso=credentials.expiry.isoformat() if credentials.expiry else None,
                ),
            )
        return credentials
