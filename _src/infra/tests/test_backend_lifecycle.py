from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backend_lifecycle.py"
PARAMETERS_PATH = Path(__file__).resolve().parents[1] / "main.parameters.json"
SPEC = importlib.util.spec_from_file_location("backend_lifecycle", SCRIPT_PATH)
assert SPEC and SPEC.loader
backend_lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend_lifecycle)


class FakeAzureCommands:
    def __init__(
        self,
        source_root: Path,
        *,
        group_states: list[bool] | None = None,
        resource_group_seed: str = "",
    ):
        self.source_root = source_root
        self.group_states = list(group_states or [False])
        self.resource_group_seed = resource_group_seed
        self.calls: list[tuple[str, ...]] = []
        self.values = {
            "AZURE_BACKEND_NAME": "jarvis-home-test-func",
            "AZURE_RESOURCE_GROUP": "rg-home-jarvis",
        }

    def __call__(
        self,
        command,
        *,
        cwd,
        capture=False,
        check=True,
        sensitive=frozenset(),
    ):
        del capture, check, sensitive
        assert cwd == self.source_root
        call = tuple(command)
        self.calls.append(call)

        stdout = ""
        returncode = 0
        if call[:3] == ("az", "account", "show") and "--query" in call:
            stdout = "subscription-123\n"
        elif call[:3] == ("azd", "env", "new"):
            (self.source_root / ".azure" / call[3]).mkdir(parents=True)
        elif call[:3] == ("azd", "env", "get-value"):
            value = self.values.get(call[3])
            if value is None:
                returncode = 1
            else:
                stdout = f"{value}\n"
        elif call[:3] == ("azd", "env", "set"):
            self.values[call[3]] = call[4]
        elif call[:3] == ("az", "storage", "account"):
            stdout = "stjarvishome\n"
        elif call[:3] == ("az", "group", "exists"):
            state = self.group_states.pop(0) if len(self.group_states) > 1 else self.group_states[0]
            stdout = f"{str(state).lower()}\n"
        elif call[:3] == ("az", "group", "show"):
            stdout = f"{self.resource_group_seed}\n"

        return subprocess.CompletedProcess(call, returncode, stdout=stdout, stderr="")


@pytest.fixture()
def lifecycle(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_lifecycle, "_require_command", lambda command: None)
    monkeypatch.setattr(backend_lifecycle, "_wait_for_health", lambda url: None)
    return tmp_path


def test_install_reuses_environment_and_generated_admin_key(monkeypatch, lifecycle):
    fake = FakeAzureCommands(lifecycle)
    monkeypatch.setattr(backend_lifecycle, "_run", fake)

    backend_lifecycle.install_backend(
        "home",
        "australiaeast",
        skip_health_check=True,
        source_root=lifecycle,
    )
    generated_key = fake.values["ADMIN_API_KEY"]
    generated_seed = fake.values["RESOURCE_NAME_SEED"]
    assert len(generated_key) >= 32
    assert len(generated_seed) == 16

    backend_lifecycle.install_backend(
        "home",
        "australiaeast",
        skip_health_check=True,
        source_root=lifecycle,
    )

    assert fake.values["ADMIN_API_KEY"] == generated_key
    assert fake.values["RESOURCE_NAME_SEED"] == generated_seed
    assert (
        "azd",
        "env",
        "new",
        "home",
        "--subscription",
        "subscription-123",
        "--location",
        "australiaeast",
        "--no-prompt",
    ) in fake.calls
    assert fake.calls.count(("azd", "env", "select", "home")) == 1
    assert fake.calls.count(("azd", "up", "--no-prompt")) == 2


def test_uninstall_is_noop_when_group_is_already_absent(monkeypatch, lifecycle):
    fake = FakeAzureCommands(lifecycle, group_states=[False])
    monkeypatch.setattr(backend_lifecycle, "_run", fake)

    backend_lifecycle.uninstall_backend(
        "home",
        confirmed=True,
        source_root=lifecycle,
    )
    backend_lifecycle.uninstall_backend(
        "home",
        confirmed=True,
        source_root=lifecycle,
    )

    assert not any(call[:3] == ("az", "group", "delete") for call in fake.calls)


def test_install_recovers_resource_seed_from_existing_group(monkeypatch, lifecycle):
    fake = FakeAzureCommands(
        lifecycle,
        group_states=[True],
        resource_group_seed="existing-seed",
    )
    monkeypatch.setattr(backend_lifecycle, "_run", fake)

    backend_lifecycle.install_backend(
        "home",
        "australiaeast",
        skip_health_check=True,
        source_root=lifecycle,
    )

    assert fake.values["RESOURCE_NAME_SEED"] == "existing-seed"


def test_uninstall_deletes_existing_group_and_verifies_absence(monkeypatch, lifecycle):
    fake = FakeAzureCommands(lifecycle, group_states=[True, False])
    monkeypatch.setattr(backend_lifecycle, "_run", fake)

    backend_lifecycle.uninstall_backend(
        "home",
        confirmed=True,
        source_root=lifecycle,
    )

    assert (
        "az",
        "group",
        "delete",
        "--name",
        "rg-home-jarvis",
        "--yes",
    ) in fake.calls


def test_uninstall_uses_azd_purge_for_a_managed_environment(monkeypatch, lifecycle):
    fake = FakeAzureCommands(lifecycle, group_states=[True, False])
    monkeypatch.setattr(backend_lifecycle, "_run", fake)
    (lifecycle / ".azure" / "home").mkdir(parents=True)

    backend_lifecycle.uninstall_backend(
        "home",
        confirmed=True,
        source_root=lifecycle,
    )

    assert ("azd", "down", "--force", "--purge", "--no-prompt") in fake.calls
    assert not any(call[:3] == ("az", "group", "delete") for call in fake.calls)
    assert fake.values["RESOURCE_NAME_SEED"] != "home"


def test_uninstall_rotates_seed_when_managed_group_is_already_absent(
    monkeypatch,
    lifecycle,
):
    fake = FakeAzureCommands(lifecycle, group_states=[False])
    fake.values["RESOURCE_NAME_SEED"] = "previous-seed"
    monkeypatch.setattr(backend_lifecycle, "_run", fake)
    (lifecycle / ".azure" / "home").mkdir(parents=True)

    backend_lifecycle.uninstall_backend(
        "home",
        confirmed=True,
        source_root=lifecycle,
    )

    assert fake.values["RESOURCE_NAME_SEED"] != "previous-seed"
    assert not any(call[:2] == ("azd", "down") for call in fake.calls)


def test_uninstall_requires_confirmation_for_existing_group(monkeypatch, lifecycle):
    fake = FakeAzureCommands(lifecycle, group_states=[True])
    monkeypatch.setattr(backend_lifecycle, "_run", fake)

    with pytest.raises(backend_lifecycle.LifecycleError, match="without --yes"):
        backend_lifecycle.uninstall_backend(
            "home",
            confirmed=False,
            source_root=lifecycle,
        )


def test_azd_profile_keeps_key_vault_protected_and_uses_lifecycle_seed():
    parameters = json.loads(PARAMETERS_PATH.read_text(encoding="utf-8"))

    assert parameters["parameters"]["enableKeyVaultPurgeProtection"]["value"] is True
    assert parameters["parameters"]["resourceNameSeed"]["value"] == (
        "${RESOURCE_NAME_SEED}"
    )
