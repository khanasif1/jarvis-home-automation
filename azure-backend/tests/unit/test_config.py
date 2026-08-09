from __future__ import annotations

import pytest

from home_assistant_api.config import AppConfig, TableStorageCredential
from home_assistant_api.errors import ConfigurationError


def test_defaults_to_development_environment():
    config = AppConfig.from_environment({})
    assert config.environment == "development"
    assert config.is_production is False


def test_production_environment_flag():
    config = AppConfig.from_environment({"APP_ENVIRONMENT": "production"})
    assert config.is_production is True


def test_require_device_tokens_missing_raises():
    config = AppConfig.from_environment({})
    with pytest.raises(ConfigurationError):
        config.require_device_tokens()


def test_require_device_tokens_parses_json():
    config = AppConfig.from_environment({"DEVICE_API_TOKENS": '{"d1": "tok1"}'})
    assert config.require_device_tokens() == {"d1": "tok1"}


def test_require_device_tokens_rejects_invalid_json():
    config = AppConfig.from_environment({"DEVICE_API_TOKENS": "not-json"})
    with pytest.raises(ConfigurationError):
        config.require_device_tokens()


def test_require_device_tokens_rejects_empty_object():
    config = AppConfig.from_environment({"DEVICE_API_TOKENS": "{}"})
    with pytest.raises(ConfigurationError):
        config.require_device_tokens()


def test_require_admin_api_key_missing_raises():
    config = AppConfig.from_environment({})
    with pytest.raises(ConfigurationError):
        config.require_admin_api_key()


def test_require_azure_openai_missing_raises():
    config = AppConfig.from_environment({})
    with pytest.raises(ConfigurationError):
        config.require_azure_openai()


def test_require_azure_openai_returns_config():
    config = AppConfig.from_environment(
        {
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_API_KEY": "key",
            "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini",
        }
    )
    aoai = config.require_azure_openai()
    assert aoai.endpoint == "https://example.openai.azure.com/"
    assert aoai.api_version == "2024-10-21"  # default applied


def test_require_speech_missing_raises():
    config = AppConfig.from_environment({})
    with pytest.raises(ConfigurationError):
        config.require_speech()


def test_google_oauth_returns_none_when_unconfigured():
    config = AppConfig.from_environment({})
    assert config.google_oauth() is None


def test_google_oauth_returns_config_when_fully_set():
    config = AppConfig.from_environment(
        {
            "GOOGLE_OAUTH_CLIENT_ID": "id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://example/callback",
        }
    )
    oauth = config.google_oauth()
    assert oauth is not None
    assert oauth.client_id == "id"
    assert len(oauth.scopes) >= 1


def test_require_google_oauth_raises_when_unconfigured():
    config = AppConfig.from_environment({})
    with pytest.raises(ConfigurationError):
        config.require_google_oauth()


def test_idempotency_and_tool_iteration_defaults():
    config = AppConfig.from_environment({})
    assert config.idempotency_ttl_seconds == 86400
    assert config.max_tool_iterations == 5


def test_idempotency_ttl_malformed_raises_configuration_error_not_value_error():
    config = AppConfig.from_environment({"IDEMPOTENCY_TTL_SECONDS": "not-a-number"})
    with pytest.raises(ConfigurationError):
        config.idempotency_ttl_seconds


def test_idempotency_ttl_zero_or_negative_raises():
    config = AppConfig.from_environment({"IDEMPOTENCY_TTL_SECONDS": "0"})
    with pytest.raises(ConfigurationError):
        config.idempotency_ttl_seconds
    config_negative = AppConfig.from_environment({"IDEMPOTENCY_TTL_SECONDS": "-5"})
    with pytest.raises(ConfigurationError):
        config_negative.idempotency_ttl_seconds


def test_idempotency_ttl_above_maximum_raises():
    config = AppConfig.from_environment({"IDEMPOTENCY_TTL_SECONDS": "999999999"})
    with pytest.raises(ConfigurationError):
        config.idempotency_ttl_seconds


def test_idempotency_ttl_accepts_valid_override():
    config = AppConfig.from_environment({"IDEMPOTENCY_TTL_SECONDS": "3600"})
    assert config.idempotency_ttl_seconds == 3600


def test_max_tool_iterations_malformed_raises_configuration_error():
    config = AppConfig.from_environment({"MAX_TOOL_ITERATIONS": "five"})
    with pytest.raises(ConfigurationError):
        config.max_tool_iterations


def test_max_tool_iterations_out_of_bounds_raises():
    config = AppConfig.from_environment({"MAX_TOOL_ITERATIONS": "0"})
    with pytest.raises(ConfigurationError):
        config.max_tool_iterations
    config_too_high = AppConfig.from_environment({"MAX_TOOL_ITERATIONS": "100"})
    with pytest.raises(ConfigurationError):
        config_too_high.max_tool_iterations


def test_reminder_poll_lookahead_allows_zero():
    config = AppConfig.from_environment({"REMINDER_POLL_LOOKAHEAD_SECONDS": "0"})
    assert config.reminder_poll_lookahead_seconds == 0


def test_reminder_poll_lookahead_negative_raises():
    config = AppConfig.from_environment({"REMINDER_POLL_LOOKAHEAD_SECONDS": "-1"})
    with pytest.raises(ConfigurationError):
        config.reminder_poll_lookahead_seconds


def test_device_tokens_absent_returns_none():
    config = AppConfig.from_environment({})
    assert config.device_tokens() is None


def test_device_tokens_present_and_valid_returns_mapping():
    config = AppConfig.from_environment({"DEVICE_API_TOKENS": '{"d1": "tok1"}'})
    assert config.device_tokens() == {"d1": "tok1"}


def test_device_tokens_malformed_json_raises_not_silently_ignored():
    config = AppConfig.from_environment({"DEVICE_API_TOKENS": "{not-json"})
    with pytest.raises(ConfigurationError):
        config.device_tokens()


def test_device_tokens_wrong_shape_raises():
    config = AppConfig.from_environment({"DEVICE_API_TOKENS": "[1, 2, 3]"})
    with pytest.raises(ConfigurationError):
        config.device_tokens()


def test_device_tokens_empty_object_raises():
    config = AppConfig.from_environment({"DEVICE_API_TOKENS": "{}"})
    with pytest.raises(ConfigurationError):
        config.device_tokens()


def test_persistence_mode_defaults_memory_in_development():
    config = AppConfig.from_environment({})
    assert config.persistence_mode == "memory"


def test_persistence_mode_defaults_table_in_production():
    config = AppConfig.from_environment({"APP_ENVIRONMENT": "production"})
    assert config.persistence_mode == "table"


def test_persistence_mode_explicit_overrides_environment_default():
    config = AppConfig.from_environment(
        {"APP_ENVIRONMENT": "production", "PERSISTENCE_MODE": "memory"}
    )
    assert config.persistence_mode == "memory"

    config_dev_table = AppConfig.from_environment(
        {"APP_ENVIRONMENT": "development", "PERSISTENCE_MODE": "table"}
    )
    assert config_dev_table.persistence_mode == "table"


def test_persistence_mode_invalid_value_raises():
    config = AppConfig.from_environment({"PERSISTENCE_MODE": "sqlite"})
    with pytest.raises(ConfigurationError):
        config.persistence_mode


def test_require_table_storage_credential_missing_in_development_raises():
    config = AppConfig.from_environment({})
    with pytest.raises(ConfigurationError):
        config.require_table_storage_credential()


def test_require_table_storage_credential_development_prefers_connection_string():
    config = AppConfig.from_environment(
        {
            "TABLE_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
            "STORAGE_TABLE_ENDPOINT": "https://example.table.core.windows.net",
        }
    )
    credential = config.require_table_storage_credential()
    assert credential == TableStorageCredential(
        mode="connection_string", connection_string="UseDevelopmentStorage=true"
    )


def test_require_table_storage_credential_development_falls_back_to_endpoint():
    config = AppConfig.from_environment(
        {"STORAGE_TABLE_ENDPOINT": "https://example.table.core.windows.net"}
    )
    credential = config.require_table_storage_credential()
    assert credential == TableStorageCredential(
        mode="endpoint", endpoint="https://example.table.core.windows.net"
    )


def test_require_table_storage_credential_production_requires_endpoint():
    config = AppConfig.from_environment(
        {
            "APP_ENVIRONMENT": "production",
            "TABLE_STORAGE_CONNECTION_STRING": "UseDevelopmentStorage=true",
        }
    )
    with pytest.raises(ConfigurationError):
        config.require_table_storage_credential()


def test_require_table_storage_credential_production_uses_endpoint():
    config = AppConfig.from_environment(
        {
            "APP_ENVIRONMENT": "production",
            "STORAGE_TABLE_ENDPOINT": "https://example.table.core.windows.net",
        }
    )
    credential = config.require_table_storage_credential()
    assert credential == TableStorageCredential(
        mode="endpoint", endpoint="https://example.table.core.windows.net"
    )


def test_require_table_storage_credential_production_ignores_azure_web_jobs_storage():
    """AzureWebJobsStorage is the Functions runtime's own storage connection
    (or, per infra, an identity-based __blobServiceUri/__queueServiceUri/
    __tableServiceUri setting) -- it must never be treated as a usable
    connection string fallback for the application's own repositories."""

    config = AppConfig.from_environment(
        {
            "APP_ENVIRONMENT": "production",
            "AzureWebJobsStorage": "UseDevelopmentStorage=true",
        }
    )
    with pytest.raises(ConfigurationError):
        config.require_table_storage_credential()


def test_require_table_storage_credential_production_ignores_flex_consumption_identity_host_settings():
    """Flex Consumption's identity-based host storage settings
    (``AzureWebJobsStorage__blobServiceUri``/``__queueServiceUri``/
    ``__tableServiceUri``) configure the Functions *host runtime* only --
    they must never be read as, or substituted for, this application's own
    ``STORAGE_TABLE_ENDPOINT`` setting. Production must still fail fast if
    ``STORAGE_TABLE_ENDPOINT`` itself is absent, even when these host
    settings are present."""

    config = AppConfig.from_environment(
        {
            "APP_ENVIRONMENT": "production",
            "AzureWebJobsStorage__blobServiceUri": "https://example.blob.core.windows.net",
            "AzureWebJobsStorage__queueServiceUri": "https://example.queue.core.windows.net",
            "AzureWebJobsStorage__tableServiceUri": "https://example.table.core.windows.net",
        }
    )
    with pytest.raises(ConfigurationError):
        config.require_table_storage_credential()


def test_require_table_storage_credential_production_with_flex_consumption_host_settings_and_endpoint_succeeds():
    """The realistic Flex Consumption production shape: identity-based host
    settings for the Functions runtime itself, plus the application's own
    ``STORAGE_TABLE_ENDPOINT`` -- and no connection string anywhere. Must
    resolve to the endpoint-based credential."""

    config = AppConfig.from_environment(
        {
            "APP_ENVIRONMENT": "production",
            "AzureWebJobsStorage__blobServiceUri": "https://example.blob.core.windows.net",
            "AzureWebJobsStorage__queueServiceUri": "https://example.queue.core.windows.net",
            "AzureWebJobsStorage__tableServiceUri": "https://example.table.core.windows.net",
            "STORAGE_TABLE_ENDPOINT": "https://example.table.core.windows.net",
        }
    )
    credential = config.require_table_storage_credential()
    assert credential == TableStorageCredential(
        mode="endpoint", endpoint="https://example.table.core.windows.net"
    )


def test_oauth_state_signing_key_prefers_dedicated_setting():
    config = AppConfig.from_environment(
        {"OAUTH_STATE_SIGNING_KEY": "dedicated-key", "ADMIN_API_KEY": "admin-key"}
    )
    assert config.oauth_state_signing_key() == "dedicated-key"


def test_oauth_state_signing_key_falls_back_to_admin_api_key():
    config = AppConfig.from_environment({"ADMIN_API_KEY": "admin-key"})
    assert config.oauth_state_signing_key() == "admin-key"


def test_oauth_state_signing_key_missing_both_raises():
    config = AppConfig.from_environment({})
    with pytest.raises(ConfigurationError):
        config.oauth_state_signing_key()
