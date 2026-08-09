#!/usr/bin/env python3
"""Idempotent install and uninstall commands for the Azure backend."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,14}[a-z0-9]$")


class LifecycleError(RuntimeError):
    """Raised when an Azure lifecycle operation cannot complete safely."""


def _format_command(command: Sequence[str], sensitive: frozenset[int]) -> str:
    return " ".join(
        "******" if index in sensitive else value
        for index, value in enumerate(command)
    )


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    capture: bool = False,
    check: bool = True,
    sensitive: frozenset[int] = frozenset(),
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        message = f"Command failed: {_format_command(command, sensitive)}"
        if detail:
            message = f"{message}\n{detail}"
        raise LifecycleError(message)
    return result


def _require_command(command: str) -> None:
    has_separator = os.path.sep in command or (
        os.path.altsep is not None and os.path.altsep in command
    )
    if (has_separator and not Path(command).is_file()) or (
        not has_separator and shutil.which(command) is None
    ):
        raise LifecycleError(f"Required command not found: {command}")


def _validate_environment_name(environment_name: str) -> None:
    if not ENVIRONMENT_NAME_PATTERN.fullmatch(environment_name):
        raise LifecycleError(
            "Environment name must be 2-16 lowercase letters, numbers, or "
            "hyphens, and must start and end with a letter or number."
        )


def _ensure_azure_login(az_command: str, *, source_root: Path) -> None:
    result = _run(
        [az_command, "account", "show", "--output", "none"],
        cwd=source_root,
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        raise LifecycleError("Azure CLI is not signed in. Run `az login` first.")


def _ensure_azd_login(azd_command: str, *, source_root: Path) -> None:
    result = _run(
        [azd_command, "auth", "login", "--check-status"],
        cwd=source_root,
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        raise LifecycleError(
            "Azure Developer CLI is not signed in. Run `azd auth login` first."
        )


def _select_subscription(
    az_command: str,
    subscription_id: str | None,
    *,
    source_root: Path,
) -> str:
    if subscription_id:
        _run(
            [az_command, "account", "set", "--subscription", subscription_id],
            cwd=source_root,
        )
        return subscription_id

    result = _run(
        [az_command, "account", "show", "--query", "id", "--output", "tsv"],
        cwd=source_root,
        capture=True,
    )
    selected = (result.stdout or "").strip()
    if not selected:
        raise LifecycleError("Azure CLI did not return a subscription ID.")
    return selected


def _ensure_azd_environment(
    azd_command: str,
    environment_name: str,
    *,
    subscription_id: str,
    location: str,
    source_root: Path,
) -> None:
    environment_dir = source_root / ".azure" / environment_name
    if environment_dir.is_dir():
        _run(
            [azd_command, "env", "select", environment_name],
            cwd=source_root,
        )
    else:
        _run(
            [
                azd_command,
                "env",
                "new",
                environment_name,
                "--subscription",
                subscription_id,
                "--location",
                location,
                "--no-prompt",
            ],
            cwd=source_root,
        )


def _get_azd_value(
    azd_command: str,
    name: str,
    *,
    source_root: Path,
) -> str:
    result = _run(
        [azd_command, "env", "get-value", name],
        cwd=source_root,
        capture=True,
        check=False,
    )
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def _set_azd_value(
    azd_command: str,
    name: str,
    value: str,
    *,
    source_root: Path,
    sensitive: bool = False,
) -> None:
    _run(
        [azd_command, "env", "set", name, value],
        cwd=source_root,
        capture=True,
        sensitive=frozenset({4}) if sensitive else frozenset(),
    )


def _new_resource_name_seed() -> str:
    return secrets.token_hex(8)


def _wait_for_health(api_base_url: str, attempts: int = 30, delay: int = 5) -> None:
    health_url = f"{api_base_url.rstrip('/')}/health"
    last_error = ""
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(health_url, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload == {"status": "ok"}:
                    print(f"Backend health check passed: {health_url}")
                    return
                last_error = f"HTTP {response.status}: {payload!r}"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(delay)
    raise LifecycleError(
        f"Backend was deployed but did not become healthy at {health_url}: "
        f"{last_error or 'no response'}"
    )


def _group_exists(
    az_command: str,
    resource_group: str,
    *,
    source_root: Path,
) -> bool:
    result = _run(
        [az_command, "group", "exists", "--name", resource_group],
        cwd=source_root,
        capture=True,
    )
    return (result.stdout or "").strip().lower() == "true"


def _get_resource_group_seed(
    az_command: str,
    resource_group: str,
    *,
    source_root: Path,
) -> str:
    result = _run(
        [
            az_command,
            "group",
            "show",
            "--name",
            resource_group,
            "--query",
            "tags.jarvisResourceNameSeed",
            "--output",
            "tsv",
        ],
        cwd=source_root,
        capture=True,
        check=False,
    )
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and value.lower() != "null" else ""


def install_backend(
    environment_name: str,
    location: str,
    *,
    subscription_id: str | None = None,
    skip_health_check: bool = False,
    source_root: Path = SOURCE_ROOT,
) -> None:
    _validate_environment_name(environment_name)
    if not location.strip():
        raise LifecycleError("Azure location cannot be empty.")

    az_command = os.environ.get("AZ_COMMAND", "az")
    azd_command = os.environ.get("AZD_COMMAND", "azd")
    _require_command(az_command)
    _require_command(azd_command)
    _ensure_azure_login(az_command, source_root=source_root)
    _ensure_azd_login(azd_command, source_root=source_root)

    selected_subscription = _select_subscription(
        az_command,
        subscription_id,
        source_root=source_root,
    )
    _ensure_azd_environment(
        azd_command,
        environment_name,
        subscription_id=selected_subscription,
        location=location,
        source_root=source_root,
    )
    _set_azd_value(
        azd_command,
        "AZURE_SUBSCRIPTION_ID",
        selected_subscription,
        source_root=source_root,
    )
    _set_azd_value(
        azd_command,
        "AZURE_LOCATION",
        location,
        source_root=source_root,
    )

    resource_name_seed = _get_azd_value(
        azd_command,
        "RESOURCE_NAME_SEED",
        source_root=source_root,
    )
    if not resource_name_seed:
        resource_group = _get_azd_value(
            azd_command,
            "AZURE_RESOURCE_GROUP",
            source_root=source_root,
        ) or f"rg-{environment_name}-jarvis"
        if _group_exists(az_command, resource_group, source_root=source_root):
            resource_name_seed = (
                _get_resource_group_seed(
                    az_command,
                    resource_group,
                    source_root=source_root,
                )
                or environment_name
            )
        else:
            resource_name_seed = _new_resource_name_seed()
            print("Generated a new Azure resource-name seed for this environment.")
    _set_azd_value(
        azd_command,
        "RESOURCE_NAME_SEED",
        resource_name_seed,
        source_root=source_root,
    )

    admin_api_key = os.environ.get("ADMIN_API_KEY", "").strip()
    if not admin_api_key:
        admin_api_key = _get_azd_value(
            azd_command,
            "ADMIN_API_KEY",
            source_root=source_root,
        )
    if not admin_api_key:
        admin_api_key = secrets.token_urlsafe(48)
        print("Generated a new administrator key in the local azd environment.")
    if len(admin_api_key) < 32:
        raise LifecycleError("ADMIN_API_KEY must contain at least 32 characters.")
    _set_azd_value(
        azd_command,
        "ADMIN_API_KEY",
        admin_api_key,
        source_root=source_root,
        sensitive=True,
    )

    print(
        f"Installing Azure backend environment '{environment_name}' "
        f"in '{location}'..."
    )
    _run([azd_command, "up", "--no-prompt"], cwd=source_root)

    resource_group = _get_azd_value(
        azd_command,
        "AZURE_RESOURCE_GROUP",
        source_root=source_root,
    ) or f"rg-{environment_name}-jarvis"
    backend_name = _get_azd_value(
        azd_command,
        "AZURE_BACKEND_NAME",
        source_root=source_root,
    )
    if not backend_name:
        result = _run(
            [
                az_command,
                "functionapp",
                "list",
                "--resource-group",
                resource_group,
                "--query",
                "[0].name",
                "--output",
                "tsv",
            ],
            cwd=source_root,
            capture=True,
        )
        backend_name = (result.stdout or "").strip()
    if not backend_name:
        raise LifecycleError("Deployment completed but no Function App was found.")

    api_base_url = f"https://{backend_name}.azurewebsites.net/api"
    if not skip_health_check:
        _wait_for_health(api_base_url)

    storage_result = _run(
        [
            az_command,
            "storage",
            "account",
            "list",
            "--resource-group",
            resource_group,
            "--query",
            "[0].name",
            "--output",
            "tsv",
        ],
        cwd=source_root,
        capture=True,
    )
    storage_account = (storage_result.stdout or "").strip()

    print("\nAzure backend installation complete.")
    print(f"Resource group : {resource_group}")
    print(f"Function App   : {backend_name}")
    print(f"API base URL   : {api_base_url}")
    if storage_account:
        print(f"Storage account: {storage_account}")
        print(
            "Provision a Pi credential with one of:\n"
            "  Bash: infra/scripts/provision-device.sh "
            f"--device-name kitchen-pi --storage-account {storage_account}\n"
            "  PowerShell: infra\\scripts\\provision-device.ps1 "
            "-DeviceName kitchen-pi "
            f"-StorageAccountName {storage_account}"
        )


def uninstall_backend(
    environment_name: str,
    *,
    resource_group: str | None = None,
    subscription_id: str | None = None,
    confirmed: bool = False,
    source_root: Path = SOURCE_ROOT,
) -> None:
    _validate_environment_name(environment_name)

    az_command = os.environ.get("AZ_COMMAND", "az")
    azd_command = os.environ.get("AZD_COMMAND", "azd")
    _require_command(az_command)
    _ensure_azure_login(az_command, source_root=source_root)
    _select_subscription(
        az_command,
        subscription_id,
        source_root=source_root,
    )

    resolved_group = resource_group or ""
    environment_dir = source_root / ".azure" / environment_name
    use_azd_down = False
    if environment_dir.is_dir():
        _require_command(azd_command)
        _run(
            [azd_command, "env", "select", environment_name],
            cwd=source_root,
        )
        environment_group = _get_azd_value(
            azd_command,
            "AZURE_RESOURCE_GROUP",
            source_root=source_root,
        )
        resource_name_seed = _get_azd_value(
            azd_command,
            "RESOURCE_NAME_SEED",
            source_root=source_root,
        )
        if not resource_name_seed:
            _set_azd_value(
                azd_command,
                "RESOURCE_NAME_SEED",
                environment_name,
                source_root=source_root,
            )
        if not resolved_group:
            resolved_group = environment_group
            use_azd_down = True
        elif environment_group and resolved_group == environment_group:
            use_azd_down = True
    resolved_group = resolved_group or f"rg-{environment_name}-jarvis"

    if not _group_exists(
        az_command,
        resolved_group,
        source_root=source_root,
    ):
        if use_azd_down:
            _set_azd_value(
                azd_command,
                "RESOURCE_NAME_SEED",
                _new_resource_name_seed(),
                source_root=source_root,
            )
        print(
            f"Azure backend resource group '{resolved_group}' is already absent; "
            "nothing to remove."
        )
        return
    if not confirmed:
        raise LifecycleError(
            f"Refusing to delete '{resolved_group}' without --yes. "
            "This permanently removes the backend and its data."
        )

    print(f"Deleting Azure backend resource group '{resolved_group}'...")
    if use_azd_down:
        _ensure_azd_login(azd_command, source_root=source_root)
        _run(
            [azd_command, "down", "--force", "--purge", "--no-prompt"],
            cwd=source_root,
        )
    else:
        try:
            _run(
                [az_command, "group", "delete", "--name", resolved_group, "--yes"],
                cwd=source_root,
            )
        except LifecycleError:
            if _group_exists(
                az_command,
                resolved_group,
                source_root=source_root,
            ):
                raise

    if _group_exists(
        az_command,
        resolved_group,
        source_root=source_root,
    ):
        raise LifecycleError(
            f"Azure still reports resource group '{resolved_group}' after deletion."
        )
    if use_azd_down:
        _set_azd_value(
            azd_command,
            "RESOURCE_NAME_SEED",
            _new_resource_name_seed(),
            source_root=source_root,
        )
    print(
        f"Azure backend resource group '{resolved_group}' and all contained "
        "services were removed."
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or uninstall the Jarvis Azure backend.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    install_parser = subparsers.add_parser(
        "install",
        help="Create/update Azure services and deploy backend code.",
    )
    install_parser.add_argument("-e", "--environment-name", required=True)
    install_parser.add_argument("-l", "--location", required=True)
    install_parser.add_argument("--subscription-id")
    install_parser.add_argument("--skip-health-check", action="store_true")

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="Delete the backend resource group and all contained services.",
    )
    uninstall_parser.add_argument("-e", "--environment-name", required=True)
    uninstall_parser.add_argument("--resource-group")
    uninstall_parser.add_argument("--subscription-id")
    uninstall_parser.add_argument("--yes", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.action == "install":
            install_backend(
                args.environment_name,
                args.location,
                subscription_id=args.subscription_id,
                skip_health_check=args.skip_health_check,
            )
        else:
            uninstall_backend(
                args.environment_name,
                resource_group=args.resource_group,
                subscription_id=args.subscription_id,
                confirmed=args.yes,
            )
    except LifecycleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
