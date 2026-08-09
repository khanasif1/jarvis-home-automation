"""Unit tests for :func:`home_assistant_api.repositories.table_storage.build_table_client`.

Proves the two supported credential shapes construct the expected
``TableClient`` without ever performing network I/O or requiring real Azure
credentials:

- ``mode == "endpoint"``: identity-based. In production
  (``is_production=True``) this must default to ``ManagedIdentityCredential``
  deterministically -- never fall through a local credential chain; in
  development (``is_production=False``) it defaults to
  ``DefaultAzureCredential`` for explicit local development against a real
  Azure Storage account. Tests also cover the always-available injected
  fake-credential-factory override, which takes precedence over both
  defaults regardless of ``is_production``.
- ``mode == "connection_string"`` (explicit local/Azurite development):
  parses a connection string, exactly as before.
"""

from __future__ import annotations

import pytest
from azure.data.tables import TableClient
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from home_assistant_api.config import TableStorageCredential
from home_assistant_api.repositories.table_storage import build_table_client


class _FakeAzureCredential:
    """Structural stand-in for ``azure.identity.DefaultAzureCredential``.

    Never makes a network call -- ``get_token`` is intentionally never
    invoked by these tests, since ``TableClient`` construction alone must
    not perform any authentication.
    """

    def get_token(self, *scopes, **kwargs):  # pragma: no cover - never invoked
        raise AssertionError("get_token should never be called at construction time")


def test_build_table_client_endpoint_mode_uses_injected_credential_factory():
    calls: list[None] = []

    def _factory():
        calls.append(None)
        return _FakeAzureCredential()

    credential = TableStorageCredential(
        mode="endpoint", endpoint="https://example.table.core.windows.net"
    )
    client = build_table_client(credential, "Devices", azure_credential_factory=_factory)

    assert isinstance(client, TableClient)
    assert client.table_name == "Devices"
    assert len(calls) == 1


def test_build_table_client_injected_factory_takes_precedence_in_production():
    """Even when ``is_production=True``, an explicitly injected factory --
    as tests always supply -- must win over the ``ManagedIdentityCredential``
    default, so tests never attempt real Azure authentication."""

    credential = TableStorageCredential(
        mode="endpoint", endpoint="https://example.table.core.windows.net"
    )
    client = build_table_client(
        credential, "Devices", is_production=True, azure_credential_factory=_FakeAzureCredential
    )
    assert isinstance(client.credential, _FakeAzureCredential)


def test_build_table_client_endpoint_mode_production_defaults_to_managed_identity():
    """Without an injected factory, ``is_production=True`` must resolve
    ``azure.identity.ManagedIdentityCredential`` -- never
    ``DefaultAzureCredential`` -- so production never falls through a local
    developer credential chain. Constructing it performs no network I/O, so
    this is safe to exercise directly."""

    credential = TableStorageCredential(
        mode="endpoint", endpoint="https://example.table.core.windows.net"
    )
    client = build_table_client(credential, "Devices", is_production=True)
    assert isinstance(client, TableClient)
    assert client.table_name == "Devices"
    assert isinstance(client.credential, ManagedIdentityCredential)


def test_build_table_client_endpoint_mode_development_defaults_to_default_azure_credential():
    """Without an injected factory, ``is_production=False`` (or omitted --
    the default) resolves ``azure.identity.DefaultAzureCredential``, for
    explicit local development against a real Azure Storage account.
    Constructing it performs no network I/O, so this is safe to exercise
    directly."""

    credential = TableStorageCredential(
        mode="endpoint", endpoint="https://example.table.core.windows.net"
    )
    client = build_table_client(credential, "Devices")
    assert isinstance(client, TableClient)
    assert client.table_name == "Devices"
    assert isinstance(client.credential, DefaultAzureCredential)

    client_explicit = build_table_client(credential, "Devices", is_production=False)
    assert isinstance(client_explicit.credential, DefaultAzureCredential)


def test_build_table_client_connection_string_mode():
    credential = TableStorageCredential(
        mode="connection_string", connection_string="UseDevelopmentStorage=true"
    )
    client = build_table_client(credential, "Todos")
    assert isinstance(client, TableClient)
    assert client.table_name == "Todos"


def test_build_table_client_endpoint_mode_without_endpoint_raises():
    credential = TableStorageCredential(mode="endpoint", endpoint=None)
    with pytest.raises(ValueError):
        build_table_client(credential, "Devices", azure_credential_factory=_FakeAzureCredential)


def test_build_table_client_connection_string_mode_without_value_raises():
    credential = TableStorageCredential(mode="connection_string", connection_string=None)
    with pytest.raises(ValueError):
        build_table_client(credential, "Devices")


def test_build_table_client_unknown_mode_raises():
    credential = TableStorageCredential(mode="bogus")
    with pytest.raises(ValueError):
        build_table_client(credential, "Devices")
