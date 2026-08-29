"""Command-line entry point for the Pi client and installation diagnostics."""

from __future__ import annotations

import argparse
import json
import logging
import math
import platform
import sys
from pathlib import Path

import numpy

from .api import ApiClient
from .audio.capture import AudioCapture, list_input_devices, resolve_input_device
from .audio.playback import (
    AudioPlayback,
    list_output_devices,
    resolve_output_device,
)
from .audio.wav import PcmAudio
from .config import ConfigError, check_file_permissions, load_config
from .main import run_forever
from .version import get_version


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="home-assistant-pi")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/etc/home-assistant-pi/config.env"),
        help="configuration file path",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run", help="run the wake-word service")
    subparsers.add_parser("doctor", help="check local configuration and dependencies")
    subparsers.add_parser("devices", help="list PortAudio input and output devices")
    subparsers.add_parser("version", help="print the installed version")
    audio_test_parser = subparsers.add_parser(
        "audio-test",
        help="record, analyze, and play back enhanced microphone audio",
    )
    audio_test_parser.add_argument(
        "--seconds",
        type=float,
        default=5.0,
        help="capture duration from 1 to 30 seconds (default: 5)",
    )
    config_parser = subparsers.add_parser(
        "print-effective-config",
        help="print configuration with the device GUID redacted",
    )
    config_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def _devices() -> int:
    try:
        inputs = list_input_devices()
        outputs = list_output_devices()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Input devices:")
    for device in inputs:
        print(f"  {device.index}: {device.name}")
    print("Output devices:")
    for device in outputs:
        print(f"  {device.index}: {device.name}")
    return 0


def _doctor(config_path: Path) -> int:
    checks: list[tuple[str, bool, str]] = []
    try:
        config = load_config(config_path)
        checks.append(("configuration", True, "valid"))
    except ConfigError as exc:
        checks.append(("configuration", False, str(exc)))
        config = None

    permission_warning = check_file_permissions(config_path)
    checks.append(
        (
            "configuration permissions",
            permission_warning is None,
            permission_warning or "restricted",
        )
    )
    is_64_bit = platform.architecture()[0] == "64bit"
    checks.append(
        (
            "64-bit operating system",
            is_64_bit,
            platform.machine() or platform.architecture()[0],
        )
    )
    try:
        import webrtcvad  # noqa: F401
        from .wakeword import create_detector

        detector = create_detector(
            config.wakeword_threshold if config is not None else 0.15,
            config.wakeword_model_path if config is not None else None,
        )
        detector.close()
        model_name = (
            config.wakeword_model_path
            if config is not None and config.wakeword_model_path
            else "built-in Jarvis ensemble"
        )
        checks.append(("wake-word model", True, f"TFLite model loaded: {model_name}"))
    except Exception as exc:
        checks.append(("wake-word model", False, str(exc)))
    if config is not None and config.audio_enhancement:
        try:
            from .audio.enhancement import validate_runtime

            validate_runtime()
            checks.append(
                ("microphone enhancement", True, "SpeexDSP noise suppression and AGC")
            )
        except Exception as exc:
            checks.append(("microphone enhancement", False, str(exc)))
    try:
        inputs = list_input_devices()
        outputs = list_output_devices()
        checks.append(
            (
                "audio devices",
                bool(inputs and outputs),
                f"{len(inputs)} input, {len(outputs)} output",
            )
        )
    except Exception as exc:
        checks.append(("audio devices", False, str(exc)))
    try:
        selected_input = resolve_input_device(
            config.input_device if config is not None else None
        )
        checks.append(
            (
                "microphone selection",
                True,
                f"{selected_input.value}: {selected_input.name}",
            )
        )
    except Exception as exc:
        checks.append(("microphone selection", False, str(exc)))
    try:
        selected_output = resolve_output_device(
            config.output_device if config is not None else None
        )
        checks.append(
            (
                "speaker selection",
                True,
                f"{selected_output.value}: {selected_output.name}",
            )
        )
    except Exception as exc:
        checks.append(("speaker selection", False, str(exc)))

    if config is not None:
        client = ApiClient(config.api_base_url, config.device_guid)
        try:
            checks.append(
                (
                    "backend health",
                    client.health(),
                    config.api_base_url,
                )
            )
        finally:
            client.close()

    for name, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    return 0 if all(item[1] for item in checks) else 1


def _resample_pcm16(audio: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate or not audio:
        return audio
    source = numpy.frombuffer(audio, dtype=numpy.int16)
    target_length = max(1, round(len(source) * target_rate / source_rate))
    source_positions = numpy.arange(len(source), dtype=numpy.float64)
    target_positions = (
        numpy.arange(target_length, dtype=numpy.float64) * source_rate / target_rate
    )
    return numpy.interp(target_positions, source_positions, source).astype(
        numpy.int16
    ).tobytes()


def _audio_test(config, seconds: float) -> int:
    if not 1.0 <= seconds <= 30.0:
        print("--seconds must be between 1 and 30.", file=sys.stderr)
        return 2

    capture = AudioCapture(
        device=config.input_device,
        enable_enhancement=config.audio_enhancement,
    )
    stream = capture.stream_chunks(320)
    frames = math.ceil(seconds * capture.sample_rate / 320)
    recorded = bytearray()
    print(
        f"Recording {seconds:g} seconds. Speak a normal query now...",
        flush=True,
    )
    try:
        try:
            for _ in range(frames):
                recorded.extend(next(stream))
        finally:
            try:
                stream.close()
            finally:
                capture.close()
    except Exception as exc:
        print(f"Audio test failed: {exc}", file=sys.stderr)
        return 1

    stats = capture.last_stats
    if stats is None:
        print("Audio capture did not produce quality statistics.", file=sys.stderr)
        return 1
    print(
        "Capture quality: "
        f"raw RMS {stats.raw_rms_dbfs:.1f} dBFS, "
        f"enhanced RMS {stats.processed_rms_dbfs:.1f} dBFS, "
        f"peak {stats.peak_dbfs:.1f} dBFS, "
        f"clipped {stats.clipped_samples}, "
        f"overflows {stats.input_overflows}, "
        f"dropped {stats.dropped_frames}"
    )
    if stats.raw_rms_dbfs < -50.0:
        print("WARN microphone level is very quiet; move closer or increase input gain.")
    if stats.peak_dbfs > -1.0 or stats.clipped_samples:
        print("WARN microphone audio is clipping; reduce input gain.")
    if stats.input_overflows or stats.dropped_frames:
        print("WARN capture lost audio frames; check Pi CPU load and audio configuration.")

    print("Playing the enhanced recording...", flush=True)
    playback_rate = 24_000
    try:
        AudioPlayback(device=config.output_device).play(
            PcmAudio(
                frames=_resample_pcm16(
                    bytes(recorded),
                    capture.sample_rate,
                    playback_rate,
                ),
                sample_rate=playback_rate,
            )
        )
    except Exception as exc:
        print(f"Audio playback test failed: {exc}", file=sys.stderr)
        return 1
    print("Audio test complete. The playback should sound clear and match your words.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "run"
    if command == "version":
        print(get_version())
        return 0
    if command == "devices":
        return _devices()
    if command == "doctor":
        return _doctor(args.config)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if command == "print-effective-config":
        values = config.safe_dict()
        if args.as_json:
            print(json.dumps(values, indent=2, sort_keys=True))
        else:
            for key, value in values.items():
                print(f"{key}={value}")
        return 0
    if command == "audio-test":
        return _audio_test(config, args.seconds)

    _configure_logging(config.log_level)
    run_forever(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
