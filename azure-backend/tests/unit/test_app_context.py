"""Composition-root tests for :mod:`home_assistant_api.app_context`.

Proves the persistence-mode-driven repository selection required for
production readiness: development defaults to in-memory repositories,
production defaults to durable Table Storage repositories, an explicit
``PERSISTENCE_MODE`` always wins, and production fails fast (no silent
in-memory fallback) when Table Storage is not configured. Production
composition is proven both against a fake ``azure_credential_factory``
(never a real network call) and, in dedicated regression tests, against
the real (but network-inert at construction time) default credential
resolution -- proving production resolves ``ManagedIdentityCredential``
deterministically and development resolves ``DefaultAzureCredential``,
never the reverse. Development/table-mode tests use
``TABLE_STORAGE_CONNECTION_STRING=UseDevelopmentStorage=true``, which
``azure-data-tables`` accepts and parses without any network I/O -- these
tests never touch a real storage account or attempt real authentication.
"""

from __future__ import annotations

import pytest

from home_assistant_api.app_context import AppContext
from home_assistant_api.config import AppConfig
from home_assistant_api.errors import ConfigurationError
from home_assistant_api.google.credentials import CredentialStore, InMemoryCredentialStorage
from home_assistant_api.repositories.devices import (
    InMemoryDevicesRepository,
    TableDevicesRepository,
)
from home_assistant_api.repositories.idempotency import (
    InMemoryIdempotencyRepository,
    TableIdempotencyRepository,
)
from home_assistant_api.repositories.reminders import (
    InMemoryRemindersRepository,
    TableRemindersRepository,
)
from home_assistant_api.repositories.sessions import (
    InMemorySessionsRepository,
    TableSessionsRepository,
)
from home_assistant_api.repositories.todos import InMemoryTodosRepository, TableTodosRepository

_FAKE_CONNECTION_STRING = "UseDevelopmentStorage=true"
_FAKE_TABLE_ENDPOINT = "https://example.table.core.windows.net"


class _FakeAzureCredential:
    """Structural stand-in for ``azure.identity.DefaultAzureCredential``.

    Never makes a network call or reads real Azure credentials -- proves
    that production composition wires an identity-based ``TableClient``
    without requiring any cloud credentials in tests.
    """

    def get_token(self, *scopes, **kwargs):  # pragma: no cover - never invoked in tests
        raise AssertionError("get_token should never be called during composition tests")


def _config(**env: str) -> AppConfig:
    return AppConfig.from_environment(env)


def test_development_default_composes_in_memory_repositories():
    ctx = AppContext(_config(APP_ENVIRONMENT="development"), seed_devices_from_config=False)
    assert isinstance(ctx.todos_repo, InMemoryTodosRepository)
    assert isinstance(ctx.reminders_repo, InMemoryRemindersRepository)
    assert isinstance(ctx.sessions_repo, InMemorySessionsRepository)
    assert isinstance(ctx.devices_repo, InMemoryDevicesRepository)
    assert isinstance(ctx.idempotency_repo, InMemoryIdempotencyRepository)
    assert isinstance(ctx.credential_store, CredentialStore)


def test_production_default_composes_table_repositories_using_identity_endpoint():
    ctx = AppContext(
        _config(
            APP_ENVIRONMENT="production",
            STORAGE_TABLE_ENDPOINT=_FAKE_TABLE_ENDPOINT,
        ),
        seed_devices_from_config=False,
        azure_credential_factory=_FakeAzureCredential,
    )
    assert isinstance(ctx.todos_repo, TableTodosRepository)
    assert isinstance(ctx.reminders_repo, TableRemindersRepository)
    assert isinstance(ctx.sessions_repo, TableSessionsRepository)
    assert isinstance(ctx.devices_repo, TableDevicesRepository)
    assert isinstance(ctx.idempotency_repo, TableIdempotencyRepository)


def test_production_without_storage_table_endpoint_fails_fast_no_silent_memory_fallback():
    with pytest.raises(ConfigurationError):
        AppContext(_config(APP_ENVIRONMENT="production"), seed_devices_from_config=False)


def test_production_default_composition_uses_managed_identity_credential_when_uninjected():
    """Regression test for deterministic production auth: when no
    ``azure_credential_factory`` is injected (as real deployed code never
    does), production composition must resolve
    ``azure.identity.ManagedIdentityCredential`` -- never
    ``DefaultAzureCredential`` -- for the Function App's own managed
    identity, matching Azure preparation guidance. Constructing the
    credential performs no network I/O, so this is safe without cloud
    credentials."""

    from azure.identity import ManagedIdentityCredential

    ctx = AppContext(
        _config(
            APP_ENVIRONMENT="production",
            STORAGE_TABLE_ENDPOINT=_FAKE_TABLE_ENDPOINT,
        ),
        seed_devices_from_config=False,
    )
    assert isinstance(ctx.todos_repo, TableTodosRepository)
    assert isinstance(ctx.todos_repo._table.credential, ManagedIdentityCredential)


def test_production_connection_string_alone_is_not_accepted_fails_fast():
    """Regression test: a connection string must never be treated as a
    usable production credential -- the provisioned storage account
    disables shared-key access, so this must fail fast rather than build a
    client that could never authenticate."""

    with pytest.raises(ConfigurationError):
        AppContext(
            _config(
                APP_ENVIRONMENT="production",
                TABLE_STORAGE_CONNECTION_STRING=_FAKE_CONNECTION_STRING,
            ),
            seed_devices_from_config=False,
        )


def test_explicit_persistence_mode_table_in_development_uses_connection_string():
    ctx = AppContext(
        _config(
            APP_ENVIRONMENT="development",
            PERSISTENCE_MODE="table",
            TABLE_STORAGE_CONNECTION_STRING=_FAKE_CONNECTION_STRING,
        ),
        seed_devices_from_config=False,
    )
    assert isinstance(ctx.devices_repo, TableDevicesRepository)


def test_explicit_persistence_mode_table_in_development_uses_identity_endpoint():
    ctx = AppContext(
        _config(
            APP_ENVIRONMENT="development",
            PERSISTENCE_MODE="table",
            STORAGE_TABLE_ENDPOINT=_FAKE_TABLE_ENDPOINT,
        ),
        seed_devices_from_config=False,
        azure_credential_factory=_FakeAzureCredential,
    )
    assert isinstance(ctx.devices_repo, TableDevicesRepository)


def test_development_identity_endpoint_composition_uses_default_azure_credential_when_uninjected():
    """Regression test: opting into identity-based table mode in
    development (no injected factory) must resolve
    ``azure.identity.DefaultAzureCredential`` -- never
    ``ManagedIdentityCredential``, which is reserved for production's own
    managed identity. Constructing the credential performs no network I/O,
    so this is safe without cloud credentials."""

    from azure.identity import DefaultAzureCredential

    ctx = AppContext(
        _config(
            APP_ENVIRONMENT="development",
            PERSISTENCE_MODE="table",
            STORAGE_TABLE_ENDPOINT=_FAKE_TABLE_ENDPOINT,
        ),
        seed_devices_from_config=False,
    )
    assert isinstance(ctx.devices_repo, TableDevicesRepository)
    assert isinstance(ctx.devices_repo._table.credential, DefaultAzureCredential)


def test_explicit_persistence_mode_memory_in_production_overrides_default():
    ctx = AppContext(
        _config(APP_ENVIRONMENT="production", PERSISTENCE_MODE="memory"),
        seed_devices_from_config=False,
    )
    assert isinstance(ctx.devices_repo, InMemoryDevicesRepository)


def test_overriding_all_repositories_never_requires_a_storage_credential_even_in_table_mode():
    """A caller (e.g. a test) that supplies every repository explicitly must
    not be forced to also configure table storage -- construction should
    never touch config.require_table_storage_credential() in that case."""

    ctx = AppContext(
        _config(APP_ENVIRONMENT="production"),
        todos_repo=InMemoryTodosRepository(),
        reminders_repo=InMemoryRemindersRepository(),
        sessions_repo=InMemorySessionsRepository(),
        devices_repo=InMemoryDevicesRepository(),
        idempotency_repo=InMemoryIdempotencyRepository(),
        credential_store=CredentialStore(InMemoryCredentialStorage()),
        seed_devices_from_config=False,
    )
    assert isinstance(ctx.todos_repo, InMemoryTodosRepository)


def test_overriding_repos_but_not_credential_backend_still_requires_storage_credential():
    """Regression test: credential storage must be included in the
    "is everything overridden" check, or this raises AssertionError instead
    of the intended ConfigurationError."""

    with pytest.raises(ConfigurationError):
        AppContext(
            _config(APP_ENVIRONMENT="production"),
            todos_repo=InMemoryTodosRepository(),
            reminders_repo=InMemoryRemindersRepository(),
            sessions_repo=InMemorySessionsRepository(),
            devices_repo=InMemoryDevicesRepository(),
            idempotency_repo=InMemoryIdempotencyRepository(),
            seed_devices_from_config=False,
        )


def test_partial_override_still_requires_storage_credential_for_the_rest():
    ctx = AppContext(
        _config(
            APP_ENVIRONMENT="production",
            STORAGE_TABLE_ENDPOINT=_FAKE_TABLE_ENDPOINT,
        ),
        devices_repo=InMemoryDevicesRepository(),
        seed_devices_from_config=False,
        azure_credential_factory=_FakeAzureCredential,
    )
    # Explicitly-overridden repo stays in-memory...
    assert isinstance(ctx.devices_repo, InMemoryDevicesRepository)
    # ...but everything else still gets the durable table implementation.
    assert isinstance(ctx.todos_repo, TableTodosRepository)
    assert isinstance(ctx.reminders_repo, TableRemindersRepository)


def test_credential_storage_override_is_respected_without_requiring_connection_string():
    ctx = AppContext(
        _config(APP_ENVIRONMENT="production"),
        todos_repo=InMemoryTodosRepository(),
        reminders_repo=InMemoryRemindersRepository(),
        sessions_repo=InMemorySessionsRepository(),
        devices_repo=InMemoryDevicesRepository(),
        idempotency_repo=InMemoryIdempotencyRepository(),
        credential_storage=InMemoryCredentialStorage(),
        seed_devices_from_config=False,
    )
    assert isinstance(ctx.credential_store, CredentialStore)


def test_seed_devices_from_config_registers_devices_from_device_api_tokens():
    ctx = AppContext(
        _config(
            APP_ENVIRONMENT="development",
            DEVICE_API_TOKENS='{"device-a": "token-a-0123456789"}',
        ),
        seed_devices_from_config=True,
    )
    assert ctx.devices_repo.get("device-a") is not None


def test_seed_devices_from_config_absent_setting_is_not_an_error():
    ctx = AppContext(
        _config(APP_ENVIRONMENT="development"),
        seed_devices_from_config=True,
    )
    assert ctx.devices_repo.list_all() == []


def test_seed_devices_from_config_malformed_setting_raises_not_silently_ignored():
    """Regression test for the fixed bug: a malformed DEVICE_API_TOKENS must
    fail process startup loudly, not be swallowed as if it were absent."""

    with pytest.raises(ConfigurationError):
        AppContext(
            _config(APP_ENVIRONMENT="development", DEVICE_API_TOKENS="{not-json"),
            seed_devices_from_config=True,
        )
