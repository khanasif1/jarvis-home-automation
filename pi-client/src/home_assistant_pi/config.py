"""Runtime configuration loading and validation for the pi-client.

Configuration is sourced from environment variables (optionally loaded from
a ``.env``-style file such as ``/etc/home-assistant-pi/config.env``). No
configuration value is ever hard-coded, and secrets are never logged or
printed in full.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

DEFAULT_CONFIG_PATH = Path("/etc/home-assistant-pi/config.env")

#: Environment variable names that must never be logged/printed verbatim.
SECRET_FIELDS = frozenset({"device_token", "api_key"})


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


def _parse_env_file(path: Path) -> dict:
    """Parse a simple ``KEY=VALUE`` env file, ignoring blanks/comments.

    Supports optional surrounding single or double quotes on values. This is
    intentionally minimal (no interpolation, no export keyword parsing
    beyond stripping it) so it has zero third-party dependencies at
    runtime beyond the standard library.
    """
    values: dict = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _api_base_url_error(url: str) -> Optional[str]:
    """Return a validation error for an unsafe or non-contract API base URL.

    The shared contract's server URL ends in ``/api``. The client appends
    component paths such as ``/voice-turn``, so accepting a host-only URL would
    silently send every request to the wrong endpoint.
    """
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        return "api_base_url is not a valid URL"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        return "api_base_url must be a valid http:// or https:// URL"
    if parsed.scheme == "http" and hostname.lower() not in _LOCAL_HOSTS:
        return (
            "api_base_url must use https:// (http:// is only permitted "
            "for localhost/127.0.0.1/::1 during local development)"
        )
    if parsed.query or parsed.fragment:
        return "api_base_url must not contain a query string or fragment"
    if parsed.path.rstrip("/") != "/api":
        return "api_base_url must end with /api"
    return None


def _validate_timezone(tz_name: str) -> Optional[str]:
    """Best-effort IANA timezone validation.

    Returns an error message if ``tz_name`` is a well-formed but unknown
    timezone. Silently returns ``None`` (skips validation) if the
    ``zoneinfo`` module or its tzdata is unavailable on this system (e.g. a
    minimal install, or Windows without the optional ``tzdata`` package),
    so a system that cannot check timezones never fails config validation
    solely because of that.
    """
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones
    except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.9+
        return None
    try:
        if not available_timezones():
            # No tzdata source available at all -- nothing to validate
            # against, so treat this as "cannot validate" rather than
            # flagging every timezone as invalid.
            return None
    except Exception:  # pragma: no cover - defensive
        return None
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return f"timezone {tz_name!r} is not a recognized IANA timezone"
    except Exception:
        # Any other failure is treated as "cannot validate" rather than
        # "invalid", per the best-effort nature of this check.
        return None
    return None


@dataclass
class Config:
    """Fully-resolved pi-client runtime configuration."""

    device_id: str
    device_token: str
    api_base_url: str

    timezone: str = "UTC"
    wakeword_engine: str = "keyboard"
    wakeword_keyword: str = "jarvis"
    wakeword_sensitivity: float = 0.5

    input_device: Optional[str] = None
    output_device: Optional[str] = None
    sample_rate: int = 16000

    reminder_poll_interval_seconds: int = 60

    log_level: str = "INFO"

    request_timeout_seconds: float = 15.0
    request_retries: int = 2

    persist_audio: bool = False

    _source_path: Optional[Path] = field(default=None, repr=False, compare=False)

    def validate(self) -> None:
        """Validate required fields and value constraints.

        Raises:
            ConfigError: if any required value is missing or malformed.
        """
        errors: list[str] = []

        if not self.device_id or not self.device_id.strip():
            errors.append("device_id is required")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", self.device_id):
            errors.append("device_id contains invalid characters")

        if not self.device_token or not self.device_token.strip():
            errors.append("device_token is required")

        if not self.api_base_url or not self.api_base_url.strip():
            errors.append("api_base_url is required")
        else:
            api_url_error = _api_base_url_error(self.api_base_url)
            if api_url_error:
                errors.append(api_url_error)

        if self.timezone and self.timezone.strip():
            tz_error = _validate_timezone(self.timezone)
            if tz_error:
                errors.append(tz_error)

        if self.sample_rate <= 0:
            errors.append("sample_rate must be a positive integer")

        if self.reminder_poll_interval_seconds <= 0:
            errors.append("reminder_poll_interval_seconds must be a positive integer")

        if not (0.0 <= self.wakeword_sensitivity <= 1.0):
            errors.append("wakeword_sensitivity must be between 0.0 and 1.0")

        if self.wakeword_engine not in {"keyboard", "porcupine", "openwakeword"}:
            errors.append(
                "wakeword_engine must be one of: keyboard, porcupine, openwakeword"
            )

        if self.request_timeout_seconds <= 0:
            errors.append("request_timeout_seconds must be positive")

        if self.request_retries < 0:
            errors.append("request_retries cannot be negative")

        if errors:
            raise ConfigError("; ".join(errors))

    def safe_dict(self) -> dict:
        """Return a dict representation with secret values masked."""
        result = {}
        for f in fields(self):
            if f.name.startswith("_"):
                continue
            value = getattr(self, f.name)
            if f.name in SECRET_FIELDS and value:
                result[f.name] = _mask_secret(str(value))
            else:
                result[f.name] = value
        return result

    def __repr__(self) -> str:  # pragma: no cover - trivial formatting
        return f"Config({self.safe_dict()!r})"


def _mask_secret(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def load_config(
    env_file: Optional[Path] = None,
    environ: Optional[dict] = None,
    validate: bool = True,
) -> Config:
    """Load configuration from an env file and/or process environment.

    Precedence (highest first): explicit ``environ`` mapping entries, then
    values found in ``env_file``, then dataclass defaults.

    Args:
        env_file: Path to a config.env style file. Defaults to
            ``/etc/home-assistant-pi/config.env`` if it exists, otherwise no
            file is read.
        environ: Mapping to use instead of ``os.environ`` (primarily for
            tests). Defaults to the real process environment.
        validate: When True (default), validate the resulting config and
            raise :class:`ConfigError` on problems.

    Returns:
        A populated :class:`Config` instance.
    """
    env_file = env_file if env_file is not None else DEFAULT_CONFIG_PATH
    file_values = _parse_env_file(env_file)
    proc_environ = os.environ if environ is None else environ

    def get(name: str, default=None):
        env_name = f"HAP_{name.upper()}"
        if env_name in proc_environ:
            return proc_environ[env_name]
        if env_name in file_values:
            return file_values[env_name]
        return default

    def get_float(name: str, default: float) -> float:
        raw = get(name, default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ConfigError(
                f"HAP_{name.upper()} must be a number, got {raw!r}"
            ) from None

    def get_int(name: str, default: int) -> int:
        raw = get(name, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ConfigError(
                f"HAP_{name.upper()} must be an integer, got {raw!r}"
            ) from None

    kwargs = {
        "device_id": get("device_id", ""),
        "device_token": get("device_token", ""),
        "api_base_url": get("api_base_url", ""),
        "timezone": get("timezone", "UTC"),
        "wakeword_engine": get("wakeword_engine", "keyboard"),
        "wakeword_keyword": get("wakeword_keyword", "jarvis"),
        "wakeword_sensitivity": get_float("wakeword_sensitivity", 0.5),
        "input_device": get("input_device") or None,
        "output_device": get("output_device") or None,
        "sample_rate": get_int("sample_rate", 16000),
        "reminder_poll_interval_seconds": get_int(
            "reminder_poll_interval_seconds", 60
        ),
        "log_level": get("log_level", "INFO"),
        "request_timeout_seconds": get_float("request_timeout_seconds", 15.0),
        "request_retries": get_int("request_retries", 2),
        "persist_audio": _to_bool(str(get("persist_audio", "false"))),
    }
    config = Config(**kwargs, _source_path=env_file if env_file.exists() else None)
    if validate:
        config.validate()
    return config


def check_file_permissions(path: Path) -> Optional[str]:
    """Return a human-readable warning if ``path`` is not securely permissioned.

    The config file is owned by ``root:homeassistant`` and installed at mode
    ``0640`` (owner read/write, group read-only, no "other" access at all) so
    the service account -- a member of the ``homeassistant`` group -- can
    read its own configuration but cannot modify it. This flags any
    group-write, group-execute, or "other" bit as insecure, while accepting
    group-read. Returns ``None`` when the file is secure or does not exist
    (nothing to check).
    """
    if not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    insecure_bits = stat.S_IWGRP | stat.S_IXGRP | stat.S_IRWXO
    if mode & insecure_bits:
        return (
            f"{path} has overly permissive mode {oct(mode)}; "
            "expected 0640 (owner read/write, group read-only, no other access)"
        )
    return None
