"""Composition root: builds and wires all runtime dependencies.

``AppContext`` is constructed once per process (module-level singleton in
``function_app.py``) but every dependency can be overridden by tests to
supply fakes -- nothing here performs network I/O until a dependency is
actually used, so importing this module or constructing an ``AppContext``
never requires cloud credentials.

Repository selection is driven by :attr:`AppConfig.persistence_mode`:
``"table"`` composes Azure Table Storage repositories reading/writing the
exact entity shape ``infra/scripts/provision-device.*`` and
``infra/modules/storage.bicep`` produce (so a device provisioned by infra
is immediately usable, and data survives a Function App restart/scale
event); ``"memory"`` composes process-local repositories for local
iteration and tests. Table Storage authenticates via
:meth:`AppConfig.require_table_storage_credential` -- identity-based
(``STORAGE_TABLE_ENDPOINT``) in production, matching the managed identity
``infra/`` grants ``Storage Table Data Contributor``; a connection string
only for explicit local/Azurite development. The identity-based token
credential itself is chosen deterministically by
:func:`~home_assistant_api.repositories.table_storage.build_table_client`:
``ManagedIdentityCredential`` in production (the Function App's own
managed identity, never a developer's local credential chain),
``DefaultAzureCredential`` only for explicit local development against a
real Azure Storage account. Building a ``TableClient`` (and the token
credential itself) performs no network I/O, so selecting table mode does
not turn constructing an ``AppContext`` into a network call.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from home_assistant_api.ai.orchestrator import ChatCompletionsClient
from home_assistant_api.auth import hash_token
from home_assistant_api.config import AppConfig, TableStorageCredential
from home_assistant_api.google.credentials import (
    CredentialStorageBackend,
    CredentialStore,
    InMemoryCredentialStorage,
    TableCredentialStorage,
)
from home_assistant_api.google.oauth import GoogleOAuthClient, require_oauth_client
from home_assistant_api.repositories.devices import (
    DevicesRepository,
    InMemoryDevicesRepository,
    TableDevicesRepository,
)
from home_assistant_api.repositories.idempotency import (
    IdempotencyRepository,
    InMemoryIdempotencyRepository,
    TableIdempotencyRepository,
)
from home_assistant_api.repositories.reminders import (
    InMemoryRemindersRepository,
    RemindersRepository,
    TableRemindersRepository,
)
from home_assistant_api.repositories.sessions import (
    InMemorySessionsRepository,
    SessionsRepository,
    TableSessionsRepository,
)
from home_assistant_api.repositories.table_storage import (
    DEVICES_TABLE_NAME,
    GOOGLE_CREDENTIALS_TABLE_NAME,
    IDEMPOTENCY_TABLE_NAME,
    REMINDERS_TABLE_NAME,
    SESSIONS_TABLE_NAME,
    TODOS_TABLE_NAME,
    build_table_client,
)
from home_assistant_api.repositories.todos import (
    InMemoryTodosRepository,
    TableTodosRepository,
    TodosRepository,
)
from home_assistant_api.speech.stt import SpeechToTextClient
from home_assistant_api.speech.tts import TextToSpeechClient
from home_assistant_api.telemetry import TelemetryClient, get_telemetry_client
from home_assistant_api.tools import ToolContext


class AppContext:
    """Process-wide runtime dependencies, built lazily and explicitly."""

    def __init__(
        self,
        config: AppConfig,
        *,
        telemetry: Optional[TelemetryClient] = None,
        todos_repo: Optional[TodosRepository] = None,
        reminders_repo: Optional[RemindersRepository] = None,
        sessions_repo: Optional[SessionsRepository] = None,
        devices_repo: Optional[DevicesRepository] = None,
        idempotency_repo: Optional[IdempotencyRepository] = None,
        credential_store: Optional[CredentialStore] = None,
        credential_storage: Optional[CredentialStorageBackend] = None,
        stt_client: Optional[SpeechToTextClient] = None,
        tts_client: Optional[TextToSpeechClient] = None,
        chat_client: Optional[ChatCompletionsClient] = None,
        chat_deployment: Optional[str] = None,
        google_oauth_client: Optional[GoogleOAuthClient] = None,
        seed_devices_from_config: bool = True,
        azure_credential_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.config = config
        self.telemetry = telemetry or get_telemetry_client()

        # Resolve the storage credential exactly once, and only when at
        # least one repository actually needs table mode -- explicitly
        # overridden repositories (as tests do) never trigger this, and a
        # development process that overrides everything never needs
        # Table Storage configured at all.
        credential_backend_overridden = credential_store is not None or credential_storage is not None
        needs_table_mode = config.persistence_mode == "table" and not all(
            [
                todos_repo,
                reminders_repo,
                sessions_repo,
                devices_repo,
                idempotency_repo,
                credential_backend_overridden,
            ]
        )
        credential: Optional[TableStorageCredential] = None
        if needs_table_mode:
            credential = config.require_table_storage_credential()

        def _table_client(table_name: str):
            assert credential is not None
            return build_table_client(
                credential,
                table_name,
                is_production=config.is_production,
                azure_credential_factory=azure_credential_factory,
            )

        use_tables = config.persistence_mode == "table"

        self.todos_repo: TodosRepository = todos_repo or (
            TableTodosRepository(_table_client(TODOS_TABLE_NAME))
            if use_tables
            else InMemoryTodosRepository()
        )
        self.reminders_repo: RemindersRepository = reminders_repo or (
            TableRemindersRepository(_table_client(REMINDERS_TABLE_NAME))
            if use_tables
            else InMemoryRemindersRepository()
        )
        self.sessions_repo: SessionsRepository = sessions_repo or (
            TableSessionsRepository(_table_client(SESSIONS_TABLE_NAME))
            if use_tables
            else InMemorySessionsRepository()
        )
        self.devices_repo: DevicesRepository = devices_repo or (
            TableDevicesRepository(_table_client(DEVICES_TABLE_NAME))
            if use_tables
            else InMemoryDevicesRepository()
        )
        self.idempotency_repo: IdempotencyRepository = idempotency_repo or (
            TableIdempotencyRepository(_table_client(IDEMPOTENCY_TABLE_NAME))
            if use_tables
            else InMemoryIdempotencyRepository()
        )

        if credential_store is not None:
            self.credential_store = credential_store
        else:
            resolved_storage: CredentialStorageBackend = credential_storage or (
                TableCredentialStorage(_table_client(GOOGLE_CREDENTIALS_TABLE_NAME))
                if use_tables
                else InMemoryCredentialStorage()
            )
            self.credential_store = CredentialStore(resolved_storage)

        self._stt_client = stt_client
        self._tts_client = tts_client
        self._chat_client = chat_client
        self._chat_deployment = chat_deployment
        self._google_oauth_client = google_oauth_client
        if seed_devices_from_config:
            self._seed_devices_from_config()

    def _seed_devices_from_config(self) -> None:
        """Optionally pre-register devices from ``DEVICE_API_TOKENS``.

        This is a convenience bootstrap path only. Devices can also be
        registered at runtime through the admin API, so an *absent*
        ``DEVICE_API_TOKENS`` setting is not an error here. A *malformed*
        value is never silently swallowed, though: ``config.device_tokens()``
        raises :class:`~home_assistant_api.errors.ConfigurationError` for
        that case and it is allowed to propagate, so a typo in this setting
        fails the process at startup instead of quietly seeding zero
        devices.
        """

        tokens = self.config.device_tokens()
        if not tokens:
            return
        for device_id, token in tokens.items():
            self.devices_repo.upsert_token(device_id, device_id, hash_token(token))

    def get_stt_client(self) -> SpeechToTextClient:
        if self._stt_client is None:
            from home_assistant_api.speech.stt import AzureSpeechToTextClient

            self._stt_client = AzureSpeechToTextClient(self.config.require_speech())
        return self._stt_client

    def get_tts_client(self) -> TextToSpeechClient:
        if self._tts_client is None:
            from home_assistant_api.speech.tts import AzureTextToSpeechClient

            self._tts_client = AzureTextToSpeechClient(self.config.require_speech())
        return self._tts_client

    def get_chat_client(self) -> tuple[ChatCompletionsClient, str]:
        if self._chat_client is None or self._chat_deployment is None:
            from openai import AzureOpenAI

            aoai_config = self.config.require_azure_openai()
            self._chat_client = AzureOpenAI(
                azure_endpoint=aoai_config.endpoint,
                api_key=aoai_config.api_key,
                api_version=aoai_config.api_version,
            )
            self._chat_deployment = aoai_config.deployment
        return self._chat_client, self._chat_deployment

    def google_configured(self) -> bool:
        return self.config.google_oauth() is not None

    def get_google_oauth_client(self) -> GoogleOAuthClient:
        if self._google_oauth_client is None:
            self._google_oauth_client = require_oauth_client(self.config.google_oauth())
        return self._google_oauth_client

    def build_tool_context(self, device_id: str) -> ToolContext:
        credential_store = self.credential_store if self.google_configured() else None
        return ToolContext(
            device_id=device_id,
            todos_repo=self.todos_repo,
            reminders_repo=self.reminders_repo,
            credential_store=credential_store,
        )


def build_default_context() -> AppContext:
    """Build the process-wide context from real environment configuration."""

    return AppContext(AppConfig.from_environment())
