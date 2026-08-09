"""Command line interface for the pi-client.

Exposes the ``home-assistant-pi`` console script with subcommands:

    home-assistant-pi --version
    home-assistant-pi doctor
    home-assistant-pi test-microphone
    home-assistant-pi test-speaker
    home-assistant-pi disk-usage
    home-assistant-pi run

Each subcommand's business logic is implemented as a plain function
returning a small result object so it can be unit tested without going
through argument parsing or touching real hardware.
"""

from __future__ import annotations

import argparse
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from .config import ConfigError, check_file_permissions, load_config
from .logging_config import configure_logging, get_logger
from .version import __version__

logger = get_logger(__name__)

DEFAULT_INSTALL_DIR = Path("/opt/home-assistant-pi")
DEFAULT_CONFIG_FILE = Path("/etc/home-assistant-pi/config.env")

SUPPORTED_ARCHITECTURES = ("armv7l", "aarch64", "arm64")


@dataclass
class CheckResult:
    """One diagnostic check performed by ``doctor``."""

    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str = ""


@dataclass
class DoctorReport:
    checks: list = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(CheckResult(name=name, status=status, detail=detail))

    @property
    def has_failures(self) -> bool:
        return any(c.status == "fail" for c in self.checks)


def run_doctor(
    env_file: Optional[Path] = None,
    check_network: bool = True,
    stdin_isatty: Optional[bool] = None,
) -> DoctorReport:
    """Run all diagnostic checks and return a report.

    Never raises for expected/anticipated problems (bad config, missing
    hardware, unreachable network); those are reported as check failures
    instead so ``doctor`` always produces useful output.

    Args:
        stdin_isatty: Overrides the interactive-terminal detection used by
            the ``wakeword_engine`` check (mainly for tests). Defaults to
            the real ``sys.stdin.isatty()`` when not given.
    """
    report = DoctorReport()
    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()

    # Python version.
    py_version = platform.python_version_tuple()
    if int(py_version[0]) == 3 and int(py_version[1]) >= 9:
        report.add("python_version", "ok", platform.python_version())
    else:
        report.add(
            "python_version",
            "warn",
            f"Python {platform.python_version()} (3.9+ recommended)",
        )

    # Architecture.
    machine = platform.machine()
    if machine in SUPPORTED_ARCHITECTURES:
        report.add("architecture", "ok", machine)
    else:
        report.add(
            "architecture",
            "warn",
            f"{machine} (expected one of {SUPPORTED_ARCHITECTURES} on a Raspberry Pi)",
        )

    # Configuration.
    env_file = env_file if env_file is not None else DEFAULT_CONFIG_FILE
    try:
        config = load_config(env_file=env_file, validate=True)
        report.add("configuration", "ok", "all required settings present")
        permission_warning = check_file_permissions(env_file)
        if permission_warning:
            report.add("config_file_permissions", "warn", permission_warning)
        else:
            report.add("config_file_permissions", "ok", str(env_file))
    except ConfigError as exc:
        report.add("configuration", "fail", str(exc))
        config = None

    # Wake-word engine production-readiness. The "keyboard" engine requires
    # an interactive terminal to receive keypresses; systemd always runs
    # services with stdin redirected from /dev/null (never a TTY), so this
    # engine can never trigger there -- the service would look "active"
    # forever while silently never detecting a wake word. Surface this
    # clearly instead of letting it run unnoticed.
    if config is not None:
        if config.wakeword_engine != "keyboard":
            report.add("wakeword_engine", "ok", config.wakeword_engine)
        elif stdin_isatty:
            report.add(
                "wakeword_engine",
                "warn",
                "'keyboard' engine is for interactive development/testing only; "
                "it cannot trigger under systemd (no interactive stdin there). "
                "Install a production engine before deploying "
                "(install.sh --wakeword-extra porcupine|openwakeword) and set "
                "wakeword_engine=porcupine or wakeword_engine=openwakeword.",
            )
        else:
            report.add(
                "wakeword_engine",
                "fail",
                "'keyboard' engine requires an interactive stdin, which is not "
                "available here (non-interactive invocation, matching how "
                "systemd runs the service) -- it will never detect a wake word. "
                "Install a production engine (install.sh --wakeword-extra "
                "porcupine|openwakeword) and set wakeword_engine=porcupine or "
                "wakeword_engine=openwakeword in config.env.",
            )

    # Audio devices.
    try:
        from .audio.capture import list_input_devices

        inputs = list_input_devices()
        if inputs:
            report.add("microphone", "ok", f"{len(inputs)} input device(s) found")
        else:
            report.add("microphone", "warn", "no input devices found")
    except Exception as exc:
        report.add("microphone", "warn", str(exc))

    try:
        from .audio.playback import list_output_devices

        outputs = list_output_devices()
        if outputs:
            report.add("speaker", "ok", f"{len(outputs)} output device(s) found")
        else:
            report.add("speaker", "warn", "no output devices found")
    except Exception as exc:
        report.add("speaker", "warn", str(exc))

    # Network reachability (best-effort, never fatal).
    if check_network and config is not None:
        try:
            import requests

            response = requests.get(
                f"{config.api_base_url.rstrip('/')}/health",
                timeout=5,
            )
            if 200 <= response.status_code < 300:
                report.add("backend_connectivity", "ok", f"HTTP {response.status_code}")
            else:
                report.add(
                    "backend_connectivity", "warn", f"HTTP {response.status_code}"
                )
        except Exception as exc:
            report.add("backend_connectivity", "warn", str(exc))

    return report


def format_doctor_report(report: DoctorReport) -> str:
    lines = ["home-assistant-pi doctor report", "=" * 32]
    for check in report.checks:
        marker = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}[check.status]
        line = f"{marker} {check.name}"
        if check.detail:
            line += f": {check.detail}"
        lines.append(line)
    return "\n".join(lines)


def cmd_test_microphone(seconds: float = 3.0, output_path: Optional[Path] = None) -> str:
    """Record a short clip from the microphone and report success/failure."""
    from .audio.capture import AudioCapture
    from .audio.vad import VoiceActivityDetector
    from .audio.wav import write_wav

    capture = AudioCapture()
    audio = capture.record_utterance(vad=VoiceActivityDetector(), max_seconds=seconds)
    message = (
        f"Captured {audio.duration_seconds:.2f}s of audio "
        f"({len(audio.frames)} bytes) at {audio.sample_rate} Hz"
    )
    if output_path is not None:
        write_wav(output_path, audio)
        message += f"; saved to {output_path}"
    return message


def cmd_test_speaker(input_path: Optional[Path] = None) -> str:
    """Play a bundled notification sound (or a given WAV file) to test the speaker."""
    from .audio.playback import AudioPlayback
    from .audio.wav import read_wav
    from .main import ASSETS_DIR

    path = input_path if input_path is not None else ASSETS_DIR / "activation.wav"
    audio = read_wav(path)
    AudioPlayback().play(audio)
    return f"Played {path} ({audio.duration_seconds:.2f}s)"


def disk_usage_bytes(path: Path) -> int:
    """Return the total size in bytes of all files under ``path``."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
    return total


def format_bytes(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def cmd_disk_usage(install_dir: Optional[Path] = None) -> str:
    install_dir = install_dir if install_dir is not None else DEFAULT_INSTALL_DIR
    total = disk_usage_bytes(install_dir)
    return f"{install_dir}: {format_bytes(total)} ({total} bytes)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="home-assistant-pi")
    parser.add_argument(
        "--version", action="store_true", help="Print the version and exit"
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("doctor", help="Report configuration and hardware status")

    mic_parser = subparsers.add_parser(
        "test-microphone", help="Record a short clip to test the microphone"
    )
    mic_parser.add_argument("--seconds", type=float, default=3.0)
    mic_parser.add_argument("--output", type=Path, default=None)

    speaker_parser = subparsers.add_parser(
        "test-speaker", help="Play a sound to test the speaker"
    )
    speaker_parser.add_argument("--input", type=Path, default=None)

    disk_parser = subparsers.add_parser(
        "disk-usage", help="Report installed application disk usage"
    )
    disk_parser.add_argument("--path", type=Path, default=None)

    subparsers.add_parser("run", help="Run the voice assistant main loop")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version or args.command is None:
        print(__version__)
        return 0

    if args.command == "doctor":
        report = run_doctor()
        print(format_doctor_report(report))
        return 1 if report.has_failures else 0

    if args.command == "test-microphone":
        try:
            print(cmd_test_microphone(seconds=args.seconds, output_path=args.output))
            return 0
        except Exception as exc:
            print(f"Microphone test failed: {exc}", file=sys.stderr)
            return 1

    if args.command == "test-speaker":
        try:
            print(cmd_test_speaker(input_path=args.input))
            return 0
        except Exception as exc:
            print(f"Speaker test failed: {exc}", file=sys.stderr)
            return 1

    if args.command == "disk-usage":
        print(cmd_disk_usage(install_dir=args.path))
        return 0

    if args.command == "run":
        try:
            config = load_config()
        except ConfigError as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 1
        configure_logging(level=config.log_level)
        from .main import run_forever
        from .wakeword.base import WakewordError

        try:
            run_forever(config)
        except WakewordError as exc:
            # Fails loudly (non-zero exit, so systemd reports the service as
            # failed) instead of silently looping forever with a wake-word
            # engine that can never actually trigger -- see run_forever().
            print(f"Wake-word engine error: {exc}", file=sys.stderr)
            return 1
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
