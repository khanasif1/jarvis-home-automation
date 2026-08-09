"""Strict configuration loading for the Azure Functions backend.

Configuration is read once from process environment variables (Azure
Function App settings in production, ``local.settings.json`` locally). There
is no silent fallback: a setting that is required for a code path but
missing raises :class:`~home_assistant_api.errors.ConfigurationError` the
first time that path is used, with a message naming the missing variable.
Endpoints that do not need a dependency (for example ``/health``) never
touch that dependency's configuration, so the app still starts cleanly
without every optional integration configured.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

from home_assistant_api.errors import ConfigurationError

TRUE_VALUES = {"1", "true", "yes", "on"}


def _get(env: Mapping[str, str], name: str) -> Optional[str]:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _require(env: Mapping[str, str], name: str) -> str:
    value = _get(env, name)
    if not value:
        raise ConfigurationError(f"Required setting '{name}' is not configured.")
    return value


def _get_bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: Optional[int] = None,
) -> int:
    """Parse an optional integer setting, enforcing sensible bounds.

    Raises:
        ConfigurationError: If the raw value is not an integer, or is
            outside ``[minimum, maximum]``. Never raises a raw ``ValueError``
            -- every misconfiguration surfaces through the same explicit
            error taxonomy as every other setting.
    """

    raw = _get(env, name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ConfigurationError(
                f"'{name}' must be an integer; got '{raw}'."
            ) from exc
    if value < minimum or (maximum is not None and value > maximum):
        bound_description = (
            f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        )
        raise ConfigurationError(f"'{name}' must be {bound_description}; got {value}.")
    return value


PERSISTENCE_MODES = ("memory", "table")

TABLE_STORAGE_CREDENTIAL_MODES = ("endpoint", "connection_string")


@dataclass(frozen=True)
class TableStorageCredential:
    """How the Table Storage repositories should authenticate.

    ``mode == "endpoint"``: identity-based, via ``endpoint`` (the storage
    account's table service URL). This is the only mode accepted in
    production, where the token credential used is
    ``ManagedIdentityCredential`` (the Function App's own managed
    identity); ``DefaultAzureCredential`` is used only when this mode is
    opted into for local development against a real Azure Storage account
    -- see
    :func:`~home_assistant_api.repositories.table_storage.build_table_client`.

    ``mode == "connection_string"``: an explicit local/Azurite development
    opt-in, via a full connection string. Never used in production.
    """

    mode: str
    endpoint: Optional[str] = None
    connection_string: Optional[str] = None


@dataclass(frozen=True)
class AzureOpenAIConfig:
    endpoint: str
    api_key: str
    deployment: str
    api_version: str


@dataclass(frozen=True)
class SpeechConfig:
    region: str
    api_key: str
    default_voice: str


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class AppConfig:
    """Lazily-validated application configuration.

    Instances are cheap: construction never fails. Each ``require_*`` method
    validates only the settings that specific dependency needs, and raises
    :class:`ConfigurationError` with an actionable message when they are
    absent.
    """

    environment: str
    _env: Mapping[str, str] = field(repr=False)

    @classmethod
    def from_environment(cls, env: Optional[Mapping[str, str]] = None) -> "AppConfig":
        source = env if env is not None else os.environ
        environment = _get(source, "APP_ENVIRONMENT") or "development"
        return cls(environment=environment, _env=dict(source))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    # -- Device authentication -------------------------------------------------
    def require_device_tokens(self) -> Mapping[str, str]:
        """Return a mapping of ``deviceId -> bearer token``.

        Configured as a single JSON object in ``DEVICE_API_TOKENS`` so
        tokens can be rotated as one Function App setting (or Key Vault
        reference) without redeploying code.
        """

        raw = _require(self._env, "DEVICE_API_TOKENS")
        return self._parse_device_tokens(raw)

    def device_tokens(self) -> Optional[Mapping[str, str]]:
        """Return the ``DEVICE_API_TOKENS`` mapping, or ``None`` if unset.

        Unlike :meth:`require_device_tokens`, an *absent* setting is not an
        error here -- devices can also be registered at runtime through the
        admin API. A *present but malformed* value (invalid JSON, wrong
        shape, empty object) always raises :class:`ConfigurationError`; it
        is never silently ignored the way an absent setting is, so a typo
        in this setting fails loudly at startup instead of quietly seeding
        zero devices.
        """

        raw = _get(self._env, "DEVICE_API_TOKENS")
        if raw is None:
            return None
        return self._parse_device_tokens(raw)

    @staticmethod
    def _parse_device_tokens(raw: str) -> Mapping[str, str]:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(
                "DEVICE_API_TOKENS must be a JSON object mapping deviceId to token."
            ) from exc
        if not isinstance(parsed, dict) or not parsed:
            raise ConfigurationError("DEVICE_API_TOKENS must be a non-empty JSON object.")
        return {str(k): str(v) for k, v in parsed.items()}

    def require_admin_api_key(self) -> str:
        return _require(self._env, "ADMIN_API_KEY")

    def oauth_state_signing_key(self) -> str:
        """Return the key used to HMAC-sign Google OAuth ``state`` values.

        Uses a dedicated ``OAUTH_STATE_SIGNING_KEY`` setting when present so
        it can be rotated independently of the admin API key; otherwise
        falls back to ``ADMIN_API_KEY`` (already required to start the OAuth
        flow, so this never introduces a new required setting).
        """

        dedicated = _get(self._env, "OAUTH_STATE_SIGNING_KEY")
        if dedicated:
            return dedicated
        return self.require_admin_api_key()

    # -- Azure OpenAI -----------------------------------------------------------
    def require_azure_openai(self) -> AzureOpenAIConfig:
        return AzureOpenAIConfig(
            endpoint=_require(self._env, "AZURE_OPENAI_ENDPOINT"),
            api_key=_require(self._env, "AZURE_OPENAI_API_KEY"),
            deployment=_require(self._env, "AZURE_OPENAI_DEPLOYMENT"),
            api_version=_get(self._env, "AZURE_OPENAI_API_VERSION") or "2024-10-21",
        )

    # -- Azure AI Speech ----------------------------------------------------------
    def require_speech(self) -> SpeechConfig:
        return SpeechConfig(
            region=_require(self._env, "SPEECH_REGION"),
            api_key=_require(self._env, "SPEECH_API_KEY"),
            default_voice=_get(self._env, "SPEECH_DEFAULT_VOICE") or "en-US-JennyNeural",
        )

    # -- Google -------------------------------------------------------------------
    def google_oauth(self) -> Optional[GoogleOAuthConfig]:
        """Return Google OAuth configuration, or ``None`` if unconfigured.

        Unlike the other ``require_*`` accessors, callers that only need to
        know *whether* Google integration is available (for example to
        decide which assistant tools to expose) should use this method. Code
        paths that actually need Google credentials must call
        :meth:`require_google_oauth` so missing configuration fails
        explicitly instead of behaving as a silent no-op.
        """

        client_id = _get(self._env, "GOOGLE_OAUTH_CLIENT_ID")
        client_secret = _get(self._env, "GOOGLE_OAUTH_CLIENT_SECRET")
        redirect_uri = _get(self._env, "GOOGLE_OAUTH_REDIRECT_URI")
        if not (client_id and client_secret and redirect_uri):
            return None
        scopes_raw = _get(self._env, "GOOGLE_OAUTH_SCOPES") or (
            "https://www.googleapis.com/auth/calendar.events "
            "https://www.googleapis.com/auth/tasks "
            "https://www.googleapis.com/auth/gmail.readonly"
        )
        return GoogleOAuthConfig(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=tuple(scopes_raw.split()),
        )

    def require_google_oauth(self) -> GoogleOAuthConfig:
        config = self.google_oauth()
        if config is None:
            raise ConfigurationError(
                "Google integration is not configured. Set GOOGLE_OAUTH_CLIENT_ID, "
                "GOOGLE_OAUTH_CLIENT_SECRET, and GOOGLE_OAUTH_REDIRECT_URI to enable it."
            )
        return config

    # -- Idempotency / turn behaviour ---------------------------------------------
    @property
    def idempotency_ttl_seconds(self) -> int:
        # Must be strictly positive: a zero or negative TTL would make every
        # completed idempotency record expire immediately, defeating the
        # replay protection ``Idempotency-Key`` exists to provide.
        return _get_bounded_int(
            self._env, "IDEMPOTENCY_TTL_SECONDS", 86400, minimum=1, maximum=2_592_000
        )

    @property
    def max_tool_iterations(self) -> int:
        # Bounded well below "unbounded": a runaway tool-calling loop should
        # fail fast with UpstreamServiceError rather than consume Azure
        # OpenAI quota indefinitely.
        return _get_bounded_int(self._env, "MAX_TOOL_ITERATIONS", 5, minimum=1, maximum=20)

    @property
    def reminder_poll_lookahead_seconds(self) -> int:
        # Zero is a valid, meaningful value (no lookahead -- only strictly
        # due reminders), so the floor is 0 rather than 1.
        return _get_bounded_int(
            self._env, "REMINDER_POLL_LOOKAHEAD_SECONDS", 0, minimum=0, maximum=86400
        )

    # -- Persistence ----------------------------------------------------------------
    @property
    def persistence_mode(self) -> str:
        """Return ``"memory"`` or ``"table"``.

        Explicit ``PERSISTENCE_MODE`` always wins. Otherwise production
        defaults to durable Table Storage repositories and development
        defaults to in-memory repositories (fast, no external dependency for
        local iteration) -- development can still opt into ``"table"`` to
        exercise the durable path locally (for example against Azurite).
        """

        raw = _get(self._env, "PERSISTENCE_MODE")
        if raw is None:
            return "table" if self.is_production else "memory"
        normalized = raw.strip().lower()
        if normalized not in PERSISTENCE_MODES:
            raise ConfigurationError(
                f"PERSISTENCE_MODE must be one of {PERSISTENCE_MODES}; got '{raw}'."
            )
        return normalized

    def require_table_storage_credential(self) -> TableStorageCredential:
        """Return how the Table Storage repositories should authenticate.

        Two mutually-exclusive credential shapes are supported:

        - ``STORAGE_TABLE_ENDPOINT`` (identity-based): the storage account's
          table endpoint URL. ``infra/`` provisions the Function App's
          managed identity with the ``Storage Table Data Contributor`` role
          on the storage account and sets this exact app setting (see
          ``infra/modules/function-app.bicep``), and disables shared-key
          access on the storage account entirely (``allowSharedKeyAccess:
          false`` in ``infra/modules/storage.bicep``) -- so a connection
          string could never authenticate against it even if one were
          configured. This is the required, and only accepted, production
          credential shape. In production the token credential used is
          ``ManagedIdentityCredential`` (the Function App's own identity,
          deterministically -- never a developer's local credential
          chain); in development, opting into this mode uses
          ``DefaultAzureCredential`` instead (for example when developing
          against a real dev storage account via ``az login``) -- see
          :func:`~home_assistant_api.repositories.table_storage.build_table_client`.
        - ``TABLE_STORAGE_CONNECTION_STRING`` (connection-string-based): an
          explicit opt-in for local development, for example against the
          Azurite emulator (``UseDevelopmentStorage=true``) or a real storage
          account that still has shared-key access enabled. Never accepted
          in production.

        In production, ``STORAGE_TABLE_ENDPOINT`` is required; a
        ``ConfigurationError`` is raised (naming the missing setting, never a
        silent fallback to a connection string or to in-memory persistence)
        when it is absent. In development, a connection string is the
        typical/explicit local path and is preferred when both are set, but
        an identity-based endpoint also works (for example when developing
        against a real dev storage account via ``az login``).
        """

        endpoint = _get(self._env, "STORAGE_TABLE_ENDPOINT")
        connection_string = _get(self._env, "TABLE_STORAGE_CONNECTION_STRING")

        if self.is_production:
            if not endpoint:
                raise ConfigurationError(
                    "Table storage is not configured for production. Set "
                    "STORAGE_TABLE_ENDPOINT to the storage account's table "
                    "endpoint so the Function App's managed identity (granted "
                    "'Storage Table Data Contributor' by infra) can authenticate "
                    "via ManagedIdentityCredential. A connection string is not "
                    "accepted in production: the provisioned storage account "
                    "disables shared key access."
                )
            return TableStorageCredential(mode="endpoint", endpoint=endpoint)

        if connection_string:
            return TableStorageCredential(mode="connection_string", connection_string=connection_string)
        if endpoint:
            return TableStorageCredential(mode="endpoint", endpoint=endpoint)
        raise ConfigurationError(
            "Table storage is not configured. Set TABLE_STORAGE_CONNECTION_STRING "
            "(for example Azurite's 'UseDevelopmentStorage=true') for local "
            "development, or STORAGE_TABLE_ENDPOINT to use identity-based "
            "authentication via DefaultAzureCredential."
        )
