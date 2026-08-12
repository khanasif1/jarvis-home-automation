"""PortAudio device discovery and deterministic selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

DeviceKind = Literal["input", "output"]


class AudioDeviceError(RuntimeError):
    """Raised when required audio hardware cannot be selected."""


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str


@dataclass(frozen=True)
class SelectedAudioDevice:
    value: int | str
    index: int | None
    name: str


def get_sounddevice():
    try:
        import sounddevice
    except Exception as exc:
        raise AudioDeviceError(f"sounddevice/PortAudio is unavailable: {exc}") from exc
    return sounddevice


def _channel_key(kind: DeviceKind) -> str:
    return "max_input_channels" if kind == "input" else "max_output_channels"


def list_audio_devices(kind: DeviceKind) -> list[AudioDevice]:
    try:
        devices = get_sounddevice().query_devices()
    except AudioDeviceError:
        raise
    except Exception as exc:
        raise AudioDeviceError(f"Could not query PortAudio devices: {exc}") from exc
    channel_key = _channel_key(kind)
    return [
        AudioDevice(index=index, name=str(info.get("name", index)))
        for index, info in enumerate(devices)
        if info.get(channel_key, 0) > 0
    ]


def _coerce_device(value: str | None) -> int | str | None:
    if value is None:
        return None
    stripped = value.strip()
    return int(stripped) if stripped.isdigit() else stripped


def _default_device(sounddevice, kind: DeviceKind) -> int | str | None:
    try:
        defaults = sounddevice.default.device
        selected = defaults[0 if kind == "input" else 1]
    except (AttributeError, IndexError, TypeError):
        return None
    if isinstance(selected, int) and selected < 0:
        return None
    return selected


def _check_device(
    sounddevice,
    kind: DeviceKind,
    value: int | str,
    *,
    sample_rate: int,
) -> SelectedAudioDevice:
    checker = (
        sounddevice.check_input_settings
        if kind == "input"
        else sounddevice.check_output_settings
    )
    checker(
        device=value,
        channels=1,
        dtype="int16",
        samplerate=sample_rate,
    )
    info = sounddevice.query_devices(value, kind=kind)
    return SelectedAudioDevice(
        value=value,
        index=value if isinstance(value, int) else None,
        name=str(info.get("name", value)),
    )


def resolve_audio_device(
    kind: DeviceKind,
    requested: str | None,
    *,
    sample_rate: int,
) -> SelectedAudioDevice:
    sounddevice = get_sounddevice()
    configured = _coerce_device(requested)
    label = "microphone" if kind == "input" else "speaker"
    variable = "HAP_INPUT_DEVICE" if kind == "input" else "HAP_OUTPUT_DEVICE"

    if configured is not None:
        try:
            return _check_device(
                sounddevice,
                kind,
                configured,
                sample_rate=sample_rate,
            )
        except Exception as exc:
            raise AudioDeviceError(
                f"Configured {label} {configured!r} cannot provide "
                f"{sample_rate} Hz mono int16 audio: {exc}. Run "
                "`home-assistant-pi devices` and correct "
                f"{variable} in /etc/home-assistant-pi/config.env."
            ) from exc

    default = _default_device(sounddevice, kind)
    if default is not None:
        try:
            return _check_device(
                sounddevice,
                kind,
                default,
                sample_rate=sample_rate,
            )
        except Exception:
            logger.warning(
                "The default %s device %r is unusable; checking available devices",
                label,
                default,
            )

    devices = list_audio_devices(kind)
    failures: list[str] = []
    for device in devices:
        try:
            selected = _check_device(
                sounddevice,
                kind,
                device.index,
                sample_rate=sample_rate,
            )
        except Exception as exc:
            failures.append(f"{device.index} ({device.name}): {exc}")
            continue
        logger.warning(
            "No usable default %s was configured; auto-selected device %s (%s). "
            "Set %s=%s to make the selection explicit.",
            label,
            device.index,
            device.name,
            variable,
            device.index,
        )
        return selected

    detail = "; ".join(failures) if failures else "no devices were reported"
    raise AudioDeviceError(
        f"No usable {label} supports {sample_rate} Hz mono int16 audio "
        f"({detail}). Connect the device, run `home-assistant-pi devices`, "
        f"and set {variable} in /etc/home-assistant-pi/config.env."
    )
