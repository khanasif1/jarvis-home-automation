"""Tests for home_assistant_pi.cli."""

from __future__ import annotations

from pathlib import Path

import pytest

import home_assistant_pi.cli as cli_mod
from home_assistant_pi.config import ConfigError
from home_assistant_pi.version import __version__


def test_disk_usage_bytes_sums_files(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("worldworld")

    total = cli_mod.disk_usage_bytes(tmp_path)
    assert total == len("hello") + len("worldworld")


def test_disk_usage_bytes_missing_path_is_zero(tmp_path: Path):
    assert cli_mod.disk_usage_bytes(tmp_path / "missing") == 0


def test_disk_usage_bytes_single_file(tmp_path: Path):
    f = tmp_path / "one.bin"
    f.write_bytes(b"1234567890")
    assert cli_mod.disk_usage_bytes(f) == 10


@pytest.mark.parametrize(
    "num_bytes,expected_unit",
    [(500, "B"), (2048, "KB"), (5 * 1024 * 1024, "MB"), (3 * 1024**3, "GB")],
)
def test_format_bytes_units(num_bytes, expected_unit):
    assert expected_unit in cli_mod.format_bytes(num_bytes)


def test_cmd_disk_usage_reports_path_and_size(tmp_path: Path):
    (tmp_path / "f.txt").write_text("x" * 100)
    output = cli_mod.cmd_disk_usage(install_dir=tmp_path)
    assert str(tmp_path) in output
    assert "100 bytes" in output


def test_main_version_flag(capsys):
    exit_code = cli_mod.main(["--version"])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_main_no_command_prints_version(capsys):
    exit_code = cli_mod.main([])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_main_disk_usage_command(tmp_path: Path, capsys):
    (tmp_path / "f.txt").write_text("data")
    exit_code = cli_mod.main(["disk-usage", "--path", str(tmp_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert str(tmp_path) in out


def test_main_run_with_invalid_config_exits_nonzero(monkeypatch, capsys):
    def fail_load_config(*args, **kwargs):
        raise ConfigError("device_id is required")

    monkeypatch.setattr(cli_mod, "load_config", fail_load_config)
    exit_code = cli_mod.main(["run"])
    assert exit_code == 1
    assert "Configuration error" in capsys.readouterr().err


def test_run_doctor_reports_configuration_failure(tmp_path: Path):
    report = cli_mod.run_doctor(
        env_file=tmp_path / "does-not-exist.env", check_network=False
    )
    assert report.has_failures
    config_check = next(c for c in report.checks if c.name == "configuration")
    assert config_check.status == "fail"


def test_run_doctor_reports_configuration_success(tmp_path: Path, monkeypatch):
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "HAP_DEVICE_ID=pi-1\nHAP_DEVICE_TOKEN=tok\nHAP_API_BASE_URL=https://api.example.com/api\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "home_assistant_pi.audio.capture.list_input_devices", lambda: []
    )
    monkeypatch.setattr(
        "home_assistant_pi.audio.playback.list_output_devices", lambda: []
    )

    # stdin_isatty=True simulates an interactive run: this test is about
    # configuration success, not wake-word production-readiness, so we
    # don't want the new keyboard-engine check (see
    # test_run_doctor_wakeword_engine_* below) to make this fail simply
    # because pytest's own stdin usually isn't a TTY.
    report = cli_mod.run_doctor(
        env_file=env_file, check_network=False, stdin_isatty=True
    )
    config_check = next(c for c in report.checks if c.name == "configuration")
    assert config_check.status == "ok"
    assert not report.has_failures


def test_format_doctor_report_contains_markers():
    report = cli_mod.run_doctor(
        env_file=Path("/nonexistent/config.env"), check_network=False
    )
    text = cli_mod.format_doctor_report(report)
    assert "home-assistant-pi doctor report" in text
    assert "[FAIL]" in text or "[WARN]" in text or "[OK]" in text


def test_build_parser_has_expected_subcommands():
    parser = cli_mod.build_parser()
    subparser_actions = [
        a for a in parser._actions if a.dest == "command"
    ]
    choices = subparser_actions[0].choices.keys()
    for expected in ("doctor", "test-microphone", "test-speaker", "disk-usage", "run"):
        assert expected in choices


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _config_env_file(tmp_path: Path) -> Path:
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "HAP_DEVICE_ID=pi-1\nHAP_DEVICE_TOKEN=tok\n"
        "HAP_API_BASE_URL=https://api.example.com/api\n",
        encoding="utf-8",
    )
    return env_file


def test_run_doctor_backend_connectivity_ok_only_for_2xx(tmp_path, monkeypatch):
    env_file = _config_env_file(tmp_path)
    monkeypatch.setattr(
        "home_assistant_pi.audio.capture.list_input_devices", lambda: []
    )
    monkeypatch.setattr(
        "home_assistant_pi.audio.playback.list_output_devices", lambda: []
    )

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200))

    report = cli_mod.run_doctor(env_file=env_file, check_network=True)
    check = next(c for c in report.checks if c.name == "backend_connectivity")
    assert check.status == "ok"


@pytest.mark.parametrize("status_code", [301, 404, 429, 500])
def test_run_doctor_backend_connectivity_warns_for_non_2xx(
    tmp_path, monkeypatch, status_code
):
    """Only a genuine 2xx should report OK; anything else -- including
    redirects and client errors that are technically < 500 -- must warn."""
    env_file = _config_env_file(tmp_path)
    monkeypatch.setattr(
        "home_assistant_pi.audio.capture.list_input_devices", lambda: []
    )
    monkeypatch.setattr(
        "home_assistant_pi.audio.playback.list_output_devices", lambda: []
    )

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(status_code))

    report = cli_mod.run_doctor(env_file=env_file, check_network=True)
    check = next(c for c in report.checks if c.name == "backend_connectivity")
    assert check.status == "warn"


def test_run_doctor_wakeword_engine_fails_when_keyboard_and_non_interactive(
    tmp_path,
):
    """The default 'keyboard' engine can never work under systemd (no TTY
    stdin there), so doctor must FAIL -- not just warn -- when it detects
    that combination, matching how the service actually runs."""
    env_file = _config_env_file(tmp_path)
    report = cli_mod.run_doctor(
        env_file=env_file, check_network=False, stdin_isatty=False
    )
    check = next(c for c in report.checks if c.name == "wakeword_engine")
    assert check.status == "fail"
    assert report.has_failures
    assert "porcupine" in check.detail or "openwakeword" in check.detail


def test_run_doctor_wakeword_engine_warns_when_keyboard_and_interactive(tmp_path):
    """Running doctor at an interactive terminal with the keyboard engine
    still deserves a warning (the real service will run non-interactively),
    but should not be a hard failure."""
    env_file = _config_env_file(tmp_path)
    report = cli_mod.run_doctor(
        env_file=env_file, check_network=False, stdin_isatty=True
    )
    check = next(c for c in report.checks if c.name == "wakeword_engine")
    assert check.status == "warn"


def test_run_doctor_wakeword_engine_ok_for_production_engine(tmp_path):
    env_file = tmp_path / "config.env"
    env_file.write_text(
        "HAP_DEVICE_ID=pi-1\nHAP_DEVICE_TOKEN=tok\n"
        "HAP_API_BASE_URL=https://api.example.com/api\n"
        "HAP_WAKEWORD_ENGINE=porcupine\n",
        encoding="utf-8",
    )
    report = cli_mod.run_doctor(
        env_file=env_file, check_network=False, stdin_isatty=False
    )
    check = next(c for c in report.checks if c.name == "wakeword_engine")
    assert check.status == "ok"
    assert not report.has_failures


def test_run_doctor_wakeword_engine_check_absent_when_config_fails(tmp_path):
    report = cli_mod.run_doctor(
        env_file=tmp_path / "does-not-exist.env",
        check_network=False,
        stdin_isatty=False,
    )
    assert all(c.name != "wakeword_engine" for c in report.checks)


def test_main_run_reports_wakeword_error_clearly(monkeypatch, capsys):
    """main(["run"]) must catch WakewordError from run_forever and print a
    clear message + exit 1, not an unhandled traceback."""
    from home_assistant_pi.config import Config
    from home_assistant_pi.wakeword.base import WakewordError

    config = Config(
        device_id="pi-1", device_token="tok", api_base_url="https://api.example.com"
    )
    monkeypatch.setattr(cli_mod, "load_config", lambda *a, **k: config)

    def fail_run_forever(*args, **kwargs):
        raise WakewordError("keyboard engine cannot run non-interactively")

    monkeypatch.setattr("home_assistant_pi.main.run_forever", fail_run_forever)

    exit_code = cli_mod.main(["run"])
    assert exit_code == 1
    assert "Wake-word engine error" in capsys.readouterr().err
