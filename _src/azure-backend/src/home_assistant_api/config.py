"""Strict environment configuration for the streaming backend."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

INPUT_SAMPLE_RATE = 16_000
OUTPUT_SAMPLE_RATE = 24_000
SAMPLE_WIDTH_BYTES = 2
MAX_COMMAND_SECONDS = 30
MAX_INPUT_BYTES = INPUT_SAMPLE_RATE * SAMPLE_WIDTH_BYTES * MAX_COMMAND_SECONDS


class ConfigurationError(ValueError):
    """Raised when required production configuration is missing or invalid."""


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required setting '{name}' is not configured.")
    return value


def canonical_uuid(value: str, *, setting_name: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ConfigurationError(f"'{setting_name}' must be a canonical UUID.") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ConfigurationError(
            f"'{setting_name}' must use lowercase canonical UUID form: {canonical}."
        )
    return canonical


def _positive_float(
    env: Mapping[str, str], name: str, default: float, *, maximum: float
) -> float:
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"'{name}' must be a number.") from exc
    if value <= 0 or value > maximum:
        raise ConfigurationError(f"'{name}' must be greater than 0 and at most {maximum}.")
    return value


@dataclass(frozen=True)
class AppConfig:
    device_guid: str
    foundry_endpoint: str
    foundry_deployment: str
    foundry_voice: str
    system_instructions: str
    response_timeout_seconds: float
    use_managed_identity: bool

    @classmethod
    def from_environment(
        cls, env: Mapping[str, str] | None = None
    ) -> "AppConfig":
        source = os.environ if env is None else env
        endpoint = _required(source, "AZURE_OPENAI_ENDPOINT").rstrip("/")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ConfigurationError(
                "'AZURE_OPENAI_ENDPOINT' must be an https:// origin without "
                "credentials, a path, query string, or fragment."
            )

        use_managed_identity = (
            source.get("AZURE_CLIENT_USE_MANAGED_IDENTITY", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        return cls(
            device_guid=canonical_uuid(
                _required(source, "DEVICE_GUID"), setting_name="DEVICE_GUID"
            ),
            foundry_endpoint=endpoint,
            foundry_deployment=_required(source, "AZURE_OPENAI_DEPLOYMENT_NAME"),
            foundry_voice=source.get("AZURE_OPENAI_VOICE", "alloy").strip() or "alloy",
            system_instructions=source.get(
                "ASSISTANT_SYSTEM_INSTRUCTIONS",
                (
                    "You are Jarvis, a concise home voice assistant. Answer clearly "
                    "in one or two short spoken sentences unless the user asks for detail."
                ),
            ).strip(),
            response_timeout_seconds=_positive_float(
                source,
                "FOUNDRY_RESPONSE_TIMEOUT_SECONDS",
                60.0,
                maximum=300.0,
            ),
            use_managed_identity=use_managed_identity,
        )

    @property
    def websocket_base_url(self) -> str:
        return (
            self.foundry_endpoint.replace("https://", "wss://", 1).rstrip("/")
            + "/openai/v1"
        )
