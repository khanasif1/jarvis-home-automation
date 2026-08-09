"""Shared Azure Table Storage plumbing for the production repositories.

Every ``Table*Repository`` in this package is a thin, explicit mapping
between the backend's domain model and one Azure Table entity shape. This
module holds only what is common to all of them:

- Resolving the storage credential (identity-based endpoint in production,
  connection string for explicit local/Azurite development -- see
  :class:`~home_assistant_api.config.TableStorageCredential`), never
  silently falling back to an unconfigured/empty value.
- Choosing the identity-based token credential deterministically:
  ``ManagedIdentityCredential`` in production, ``DefaultAzureCredential``
  only for explicit local development against a real Azure Storage
  account (see :func:`build_table_client`).
- Constructing a ``TableClient`` for a given table (no network I/O happens
  at construction time -- see :func:`build_table_client`).
- Lazily-and-idempotently ensuring the table exists on first real use. In
  production every table is IaC-provisioned by
  ``infra/modules/storage.bicep`` ahead of time, so this call is
  effectively a harmless no-op there (see :func:`ensure_table_exists`); for
  local/Azurite development, where nothing pre-provisions tables, this is
  the actual mechanism that creates them.
- Translating ``azure.core.exceptions`` into this backend's explicit error
  taxonomy so callers never see SDK-specific exception types.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from azure.core.exceptions import (
    HttpResponseError,
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)
from azure.data.tables import TableClient

from home_assistant_api.config import TableStorageCredential
from home_assistant_api.errors import UpstreamServiceError

# Table names match exactly what ``infra/modules/storage.bicep`` provisions
# (see its ``tableNames`` parameter) -- including ``GoogleCredentials`` --
# so the production repositories read and write the same tables devices are
# provisioned into by ``infra/scripts/provision-device.*``. Production
# relies on these being IaC-provisioned ahead of time; see
# :func:`ensure_table_exists` for why the lazy create-on-first-use it also
# performs is a safe no-op there (and a convenience for local/Azurite use
# where nothing pre-provisions the tables).
DEVICES_TABLE_NAME = "Devices"
TODOS_TABLE_NAME = "Todos"
REMINDERS_TABLE_NAME = "Reminders"
SESSIONS_TABLE_NAME = "Sessions"
IDEMPOTENCY_TABLE_NAME = "Idempotency"
GOOGLE_CREDENTIALS_TABLE_NAME = "GoogleCredentials"

# Devices provisioned by infra/scripts/provision-device.* always use this
# PartitionKey.
DEVICE_PARTITION_KEY = "device"


def build_table_client(
    credential: TableStorageCredential,
    table_name: str,
    *,
    is_production: bool = False,
    azure_credential_factory: Optional[Callable[[], Any]] = None,
) -> TableClient:
    """Construct a ``TableClient`` for ``table_name`` from ``credential``.

    This performs no network I/O -- it only builds the client object (and,
    for identity-based credentials, constructs a lazy ``TokenCredential``
    that does not itself make a network call until a token is actually
    requested). The underlying table is created lazily, on first real
    operation, by :func:`ensure_table_exists`.

    ``azure_credential_factory`` lets callers (production code and tests
    alike) supply the token credential used for ``mode == "endpoint"``
    without this module hard-depending on ``azure.identity`` at import
    time. When left unset, the default token credential is chosen
    deterministically from ``is_production``:

    - ``is_production=True`` -> ``ManagedIdentityCredential``. Production
      (Flex Consumption) only ever authenticates as the Function App's own
      user-assigned/system-assigned managed identity -- see
      ``infra/modules/role-assignments.bicep``, which grants that identity
      ``Storage Table Data Contributor``. ``ManagedIdentityCredential`` is
      deterministic: it does not fall through a chain of unrelated local
      credential sources (CLI/VS Code/env vars/etc.) the way
      ``DefaultAzureCredential`` does, so production never accidentally
      authenticates as a developer's own identity.
    - ``is_production=False`` -> ``DefaultAzureCredential``, used only for
      explicit local development against a real Azure Storage account via
      ``STORAGE_TABLE_ENDPOINT`` (e.g. developer ``az login`` credentials).
      Local development against Azurite should use
      ``TABLE_STORAGE_CONNECTION_STRING`` (``mode == "connection_string"``)
      instead, which never reaches this branch.

    Tests always inject a fake credential object via
    ``azure_credential_factory`` so no real Azure authentication is ever
    attempted.
    """

    if credential.mode == "endpoint":
        if credential.endpoint is None:
            raise ValueError("TableStorageCredential(mode='endpoint') requires 'endpoint'.")
        if azure_credential_factory is None:
            if is_production:
                from azure.identity import ManagedIdentityCredential

                azure_credential_factory = ManagedIdentityCredential
            else:
                from azure.identity import DefaultAzureCredential

                azure_credential_factory = DefaultAzureCredential
        return TableClient(
            endpoint=credential.endpoint,
            table_name=table_name,
            credential=azure_credential_factory(),
        )
    if credential.mode == "connection_string":
        if credential.connection_string is None:
            raise ValueError(
                "TableStorageCredential(mode='connection_string') requires 'connection_string'."
            )
        return TableClient.from_connection_string(credential.connection_string, table_name)
    raise ValueError(f"Unknown TableStorageCredential mode: {credential.mode!r}")


def ensure_table_exists(table_client: TableClient) -> None:
    """Idempotently create the table backing ``table_client``.

    Production relies on ``infra/modules/storage.bicep`` provisioning every
    table this backend uses (``Devices``, ``Todos``, ``Reminders``,
    ``Sessions``, ``Idempotency``, ``GoogleCredentials``) as infrastructure
    -- this call is not production's mechanism for making a table exist.
    It remains safe and harmless there regardless: the ``Storage Table Data
    Contributor`` role already granted to the Function App's managed
    identity (``infra/modules/role-assignments.bicep``) includes the
    table-management data actions this call would need, and hitting an
    already-provisioned table simply raises the swallowed
    ``ResourceExistsError`` below with no other effect -- so calling it does
    not require any additional managed-identity permission and does not
    risk breaking managed-identity auth.

    For local/Azurite development, where nothing pre-provisions tables,
    this lazy create-on-first-use is the actual mechanism that makes a
    table exist.

    Safe to call repeatedly and from multiple worker processes: a
    ``ResourceExistsError`` (the table already exists) is the expected,
    non-error outcome -- whether that's a concurrent create from another
    worker or a table IaC already provisioned -- and is the only exception
    this swallows. Any other failure (auth, network, throttling) propagates
    as :class:`UpstreamServiceError`.
    """

    try:
        table_client.create_table()
    except ResourceExistsError:
        return
    except HttpResponseError as exc:
        raise UpstreamServiceError(
            f"Failed to ensure table storage table '{table_client.table_name}' exists."
        ) from exc


def raise_upstream_error(operation: str, table_name: str, exc: HttpResponseError) -> None:
    """Translate an unexpected Table Storage failure into ``UpstreamServiceError``."""

    raise UpstreamServiceError(
        f"Table storage operation '{operation}' failed against table '{table_name}'."
    ) from exc


__all__ = [
    "DEVICES_TABLE_NAME",
    "TODOS_TABLE_NAME",
    "REMINDERS_TABLE_NAME",
    "SESSIONS_TABLE_NAME",
    "IDEMPOTENCY_TABLE_NAME",
    "GOOGLE_CREDENTIALS_TABLE_NAME",
    "DEVICE_PARTITION_KEY",
    "build_table_client",
    "ensure_table_exists",
    "raise_upstream_error",
    "ResourceExistsError",
    "ResourceNotFoundError",
    "ResourceModifiedError",
    "HttpResponseError",
]


class TableBackedRepositoryMixin:
    """Shared "ensure table exists exactly once" bookkeeping.

    Every concrete ``Table*Repository`` composes this alongside its own
    ``TableClient`` (accessible as ``self._table``) so the table-creation
    call happens lazily on first use and only once per process, without any
    of them duplicating the guard logic.
    """

    _table: TableClient
    _table_ensured: bool

    def _ensure_table(self) -> None:
        if not self._table_ensured:
            ensure_table_exists(self._table)
            self._table_ensured = True


def entity_to_plain_dict(entity: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a Table Storage entity into a plain ``dict`` (drops SDK metadata)."""

    return {key: value for key, value in entity.items() if not key.startswith("odata.")}
