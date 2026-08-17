"""Command-line entry point for the Pi client and installation diagnostics."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
from pathlib import Path

from .api import ApiClient
from .audio.capture import list_input_devices, resolve_input_device
from .audio.playback import list_output_devices, resolve_output_device
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
            else "built-in hey_jarvis"
        )
        checks.append(("wake-word model", True, f"TFLite model loaded: {model_name}"))
    except Exception as exc:
        checks.append(("wake-word model", False, str(exc)))
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

    _configure_logging(config.log_level)
    run_forever(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
