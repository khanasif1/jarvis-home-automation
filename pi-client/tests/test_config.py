"""Tests for home_assistant_pi.config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from home_assistant_pi.config import (
    Config,
    ConfigError,
    check_file_permissions,
    load_config,
)

posix_only = pytest.mark.skipif(
    os.name != "posix",
    reason="File permission bits (group/other) are not meaningfully enforced on Windows",
)


def _tzdata_available() -> bool:
    try:
        from zoneinfo import available_timezones

        return bool(available_timezones())
    except Exception:
        return False


requires_tzdata = pytest.mark.skipif(
    not _tzdata_available(),
    reason="No IANA tzdata source available on this system to validate against",
)


def test_load_config_from_env_file(tmp_path: Path):
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "HAP_DEVICE_ID=pi-001",
                'HAP_DEVICE_TOKEN="super-secret-token"',
                "HAP_API_BASE_URL=https://api.example.com/api",
                "HAP_SAMPLE_RATE=8000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(env_file=env_file, environ={})
    assert config.device_id == "pi-001"
    assert config.device_token == "super-secret-token"
    assert config.api_base_url == "https://api.example.com/api"
    assert config.sample_rate == 8000
    assert config.timezone == "UTC"  # default preserved


def test_environ_overrides_env_file(tmp_path: Path):
    env_file = tmp_path / "config.env"
    env_file.write_text("HAP_DEVICE_ID=from-file\n", encoding="utf-8")
    config = load_config(
        env_file=env_file,
        environ={
            "HAP_DEVICE_ID": "from-environ",
            "HAP_DEVICE_TOKEN": "token",
            "HAP_API_BASE_URL": "https://api.example.com/api",
        },
    )
    assert config.device_id == "from-environ"


def test_missing_required_fields_raises_config_error():
    with pytest.raises(ConfigError) as exc_info:
        load_config(env_file=Path("/nonexistent/config.env"), environ={})
    message = str(exc_info.value)
    assert "device_id" in message
    assert "device_token" in message
    assert "api_base_url" in message


def test_invalid_api_base_url_rejected():
    with pytest.raises(ConfigError, match="api_base_url"):
        load_config(
            env_file=Path("/nonexistent/config.env"),
            environ={
                "HAP_DEVICE_ID": "pi-1",
                "HAP_DEVICE_TOKEN": "token",
                "HAP_API_BASE_URL": "not-a-url",
            },
        )


def test_non_local_http_api_base_url_rejected():
    """Plain http:// must be rejected for any non-local host so device
    tokens are never sent unencrypted."""
    with pytest.raises(ConfigError, match="https"):
        load_config(
            env_file=Path("/nonexistent/config.env"),
            environ={
                "HAP_DEVICE_ID": "pi-1",
                "HAP_DEVICE_TOKEN": "token",
                "HAP_API_BASE_URL": "http://api.example.com/api",
            },
        )


def test_local_http_api_base_url_accepted():
    """http:// is fine for local development against localhost/127.0.0.1."""
    config = load_config(
        env_file=Path("/nonexistent/config.env"),
        environ={
            "HAP_DEVICE_ID": "pi-1",
            "HAP_DEVICE_TOKEN": "token",
            "HAP_API_BASE_URL": "http://127.0.0.1:7071/api",
        },
    )
    assert config.api_base_url == "http://127.0.0.1:7071/api"


def test_https_non_local_api_base_url_accepted():
    config = load_config(
        env_file=Path("/nonexistent/config.env"),
        environ={
            "HAP_DEVICE_ID": "pi-1",
            "HAP_DEVICE_TOKEN": "token",
            "HAP_API_BASE_URL": "https://api.example.com/api",
        },
    )
    assert config.api_base_url == "https://api.example.com/api"


def test_api_base_url_without_contract_prefix_rejected():
    with pytest.raises(ConfigError, match="end with /api"):
        load_config(
            env_file=Path("/nonexistent/config.env"),
            environ={
                "HAP_DEVICE_ID": "pi-1",
                "HAP_DEVICE_TOKEN": "token",
                "HAP_API_BASE_URL": "https://api.example.com",
            },
        )


def test_local_ipv6_http_api_base_url_accepted():
    config = load_config(
        env_file=Path("/nonexistent/config.env"),
        environ={
            "HAP_DEVICE_ID": "pi-1",
            "HAP_DEVICE_TOKEN": "token",
            "HAP_API_BASE_URL": "http://[::1]:7071/api/",
        },
    )
    assert config.api_base_url == "http://[::1]:7071/api/"


@requires_tzdata
def test_invalid_timezone_rejected():
    with pytest.raises(ConfigError, match="timezone"):
        load_config(
            env_file=Path("/nonexistent/config.env"),
            environ={
                "HAP_DEVICE_ID": "pi-1",
                "HAP_DEVICE_TOKEN": "token",
                "HAP_API_BASE_URL": "https://api.example.com/api",
                "HAP_TIMEZONE": "Not/A_Real_Zone",
            },
        )


@requires_tzdata
def test_valid_timezone_accepted():
    config = load_config(
        env_file=Path("/nonexistent/config.env"),
        environ={
            "HAP_DEVICE_ID": "pi-1",
            "HAP_DEVICE_TOKEN": "token",
            "HAP_API_BASE_URL": "https://api.example.com/api",
            "HAP_TIMEZONE": "America/New_York",
        },
    )
    assert config.timezone == "America/New_York"


def test_malformed_numeric_env_var_becomes_config_error():
    """A malformed numeric value must raise ConfigError, never a raw
    ValueError/TypeError, so callers like `doctor` never crash."""
    with pytest.raises(ConfigError, match="HAP_SAMPLE_RATE"):
        load_config(
            env_file=Path("/nonexistent/config.env"),
            environ={
                "HAP_DEVICE_ID": "pi-1",
                "HAP_DEVICE_TOKEN": "token",
                "HAP_API_BASE_URL": "https://api.example.com/api",
                "HAP_SAMPLE_RATE": "not-a-number",
            },
        )


def test_malformed_float_env_var_becomes_config_error():
    with pytest.raises(ConfigError, match="HAP_WAKEWORD_SENSITIVITY"):
        load_config(
            env_file=Path("/nonexistent/config.env"),
            environ={
                "HAP_DEVICE_ID": "pi-1",
                "HAP_DEVICE_TOKEN": "token",
                "HAP_API_BASE_URL": "https://api.example.com/api",
                "HAP_WAKEWORD_SENSITIVITY": "not-a-float",
            },
        )


def test_invalid_wakeword_engine_rejected():
    with pytest.raises(ConfigError, match="wakeword_engine"):
        load_config(
            env_file=Path("/nonexistent/config.env"),
            environ={
                "HAP_DEVICE_ID": "pi-1",
                "HAP_DEVICE_TOKEN": "token",
                "HAP_API_BASE_URL": "https://api.example.com/api",
                "HAP_WAKEWORD_ENGINE": "not-a-real-engine",
            },
        )


def test_safe_dict_masks_secrets():
    config = Config(
        device_id="pi-1",
        device_token="abcdefghijklmnop",
        api_base_url="https://api.example.com",
    )
    safe = config.safe_dict()
    assert safe["device_token"] != "abcdefghijklmnop"
    assert safe["device_token"].startswith("ab")
    assert safe["device_token"].endswith("op")
    assert "abcdefghijklmnop" not in repr(config)


def test_safe_dict_masks_very_short_secret():
    config = Config(
        device_id="pi-1",
        device_token="ab",
        api_base_url="https://api.example.com",
    )
    assert config.safe_dict()["device_token"] == "**"


@posix_only
def test_check_file_permissions_flags_world_readable(tmp_path: Path):
    path = tmp_path / "config.env"
    path.write_text("HAP_DEVICE_ID=x\n", encoding="utf-8")
    path.chmod(0o644)
    warning = check_file_permissions(path)
    assert warning is not None
    assert "0640" in warning


@posix_only
def test_check_file_permissions_accepts_owner_only(tmp_path: Path):
    path = tmp_path / "config.env"
    path.write_text("HAP_DEVICE_ID=x\n", encoding="utf-8")
    path.chmod(0o600)
    assert check_file_permissions(path) is None


@posix_only
def test_check_file_permissions_accepts_group_readable(tmp_path: Path):
    """0640 (root:homeassistant, group read-only) is the target production
    mode and must be accepted, not flagged."""
    path = tmp_path / "config.env"
    path.write_text("HAP_DEVICE_ID=x\n", encoding="utf-8")
    path.chmod(0o640)
    assert check_file_permissions(path) is None


@posix_only
def test_check_file_permissions_flags_group_writable(tmp_path: Path):
    path = tmp_path / "config.env"
    path.write_text("HAP_DEVICE_ID=x\n", encoding="utf-8")
    path.chmod(0o660)
    warning = check_file_permissions(path)
    assert warning is not None


def test_check_file_permissions_missing_file_is_none(tmp_path: Path):
    assert check_file_permissions(tmp_path / "does-not-exist.env") is None
