"""Small, strict runtime configuration for the Raspberry Pi client."""

from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

DEFAULT_CONFIG_PATH = Path("/etc/home-assistant-pi/config.env")
INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
SAMPLE_WIDTH_BYTES = 2
FRAME_DURATION_MS = 20
MAX_ALLOWED_COMMAND_SECONDS = 30.0
MAX_FOLLOWUP_TIMEOUT_SECONDS = 60.0
SECRET_FIELDS = frozenset({"device_guid"})


class ConfigError(ValueError):
    """Raised when Pi configuration is missing or unsafe."""


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _canonical_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ConfigError("device_guid must be a canonical UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ConfigError(f"device_guid must use canonical lowercase form: {canonical}")
    return canonical


def _validate_api_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ConfigError("api_base_url is not a valid URL") from exc
    localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConfigError("api_base_url must be a valid http:// or https:// URL")
    if parsed.scheme != "https" and not localhost:
        raise ConfigError("api_base_url must use HTTPS outside local development")
    if parsed.path.rstrip("/") != "/api" or parsed.query or parsed.fragment:
        raise ConfigError("api_base_url must end with /api and contain no query or fragment")


@dataclass(frozen=True)
class Config:
    api_base_url: str
    device_guid: str
    input_device: str | None = None
    output_device: str | None = None
    wakeword_threshold: float = 0.35
    wakeword_model_path: str | None = None
    vad_mode: int = 2
    no_speech_timeout_seconds: float = 3.0
    followup_timeout_seconds: float = 30.0
    silence_timeout_seconds: float = 1.2
    max_command_seconds: float = 30.0
    playback_cooldown_seconds: float = 0.75
    log_level: str = "INFO"
    _source_path: Path | None = field(default=None, repr=False, compare=False)

    def validate(self) -> None:
        errors: list[str] = []
        try:
            _validate_api_url(self.api_base_url)
        except ConfigError as exc:
            errors.append(str(exc))
        try:
            _canonical_uuid(self.device_guid)
        except ConfigError as exc:
            errors.append(str(exc))

        if not 0.0 < self.wakeword_threshold <= 1.0:
            errors.append("wakeword_threshold must be greater than 0 and at most 1")
        if self.wakeword_model_path is not None:
            model_path = Path(self.wakeword_model_path)
            if not model_path.is_absolute():
                errors.append("wakeword_model_path must be an absolute path")
            elif model_path.suffix.lower() != ".tflite":
                errors.append("wakeword_model_path must reference a .tflite file")
            elif not model_path.is_file():
                errors.append(f"wakeword_model_path does not exist: {model_path}")
        if self.vad_mode not in {0, 1, 2, 3}:
            errors.append("vad_mode must be 0, 1, 2, or 3")
        if self.no_speech_timeout_seconds <= 0:
            errors.append("no_speech_timeout_seconds must be positive")
        if not 1.0 <= self.followup_timeout_seconds <= MAX_FOLLOWUP_TIMEOUT_SECONDS:
            errors.append("followup_timeout_seconds must be between 1 and 60")
        if self.silence_timeout_seconds <= 0:
            errors.append("silence_timeout_seconds must be positive")
        if not 1.0 <= self.max_command_seconds <= MAX_ALLOWED_COMMAND_SECONDS:
            errors.append("max_command_seconds must be between 1 and 30")
        if self.no_speech_timeout_seconds > self.max_command_seconds:
            errors.append("no_speech_timeout_seconds cannot exceed max_command_seconds")
        if self.playback_cooldown_seconds < 0:
            errors.append("playback_cooldown_seconds cannot be negative")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append("log_level is invalid")
        if errors:
            raise ConfigError("; ".join(errors))

    def safe_dict(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for item in fields(self):
            if item.name.startswith("_"):
                continue
            value = getattr(self, item.name)
            result[item.name] = "********" if item.name in SECRET_FIELDS and value else value
        return result


def load_config(
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Config:
    path = DEFAULT_CONFIG_PATH if env_file is None else env_file
    file_values = _parse_env_file(path)
    process_values = os.environ if environ is None else environ

    def get(name: str, default: str = "") -> str:
        variable = f"HAP_{name.upper()}"
        if variable in process_values:
            return process_values[variable]
        return file_values.get(variable, default)

    def get_float(name: str, default: float) -> float:
        raw = get(name, str(default))
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"HAP_{name.upper()} must be a number") from exc

    def get_int(name: str, default: int) -> int:
        raw = get(name, str(default))
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"HAP_{name.upper()} must be an integer") from exc

    config = Config(
        api_base_url=get("api_base_url").rstrip("/"),
        device_guid=get("device_guid"),
        input_device=get("input_device") or None,
        output_device=get("output_device") or None,
        wakeword_threshold=get_float("wakeword_threshold", 0.35),
        wakeword_model_path=get("wakeword_model_path") or None,
        vad_mode=get_int("vad_mode", 2),
        no_speech_timeout_seconds=get_float("no_speech_timeout_seconds", 3.0),
        followup_timeout_seconds=get_float("followup_timeout_seconds", 30.0),
        silence_timeout_seconds=get_float("silence_timeout_seconds", 1.2),
        max_command_seconds=get_float("max_command_seconds", 30.0),
        playback_cooldown_seconds=get_float("playback_cooldown_seconds", 0.75),
        log_level=get("log_level", "INFO").upper(),
        _source_path=path if path.exists() else None,
    )
    config.validate()
    return config


def check_file_permissions(path: Path) -> str | None:
    if not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IWGRP | stat.S_IXGRP | stat.S_IRWXO):
        return f"{path} has mode {oct(mode)}; expected 0640"
    return None
