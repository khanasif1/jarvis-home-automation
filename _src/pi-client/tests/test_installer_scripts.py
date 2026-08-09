from __future__ import annotations

from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def test_installer_retains_release_wheel_for_idempotent_reruns():
    installer = (SCRIPTS_DIR / "install.sh").read_text(encoding="utf-8")

    assert 'rm -f "${WHEEL_FILE}"' not in installer
    assert "release bundle remains rerunnable" in installer


def test_uninstaller_tolerates_resources_that_are_already_absent():
    uninstaller = (SCRIPTS_DIR / "uninstall.sh").read_text(encoding="utf-8")

    assert 'if [[ -f "${SYSTEMD_UNIT_PATH}" ]]' in uninstaller
    assert 'if [[ -d "${INSTALL_DIR}" ]]' in uninstaller
    assert 'if [[ -d "${CONFIG_DIR}" ]]' in uninstaller
    assert 'if id -u "${SERVICE_USER}"' in uninstaller
    assert 'if getent group "${SERVICE_GROUP}"' in uninstaller
