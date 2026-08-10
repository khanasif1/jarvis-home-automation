#!/usr/bin/env python3
"""Idempotently install or remove the complete Jarvis Azure backend."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SOURCE_ROOT = Path(__file__).resolve().parents[2]
INFRA_ROOT = SOURCE_ROOT / "infra"
BACKEND_ROOT = SOURCE_ROOT / "azure-backend"
STATE_ROOT = Path.home() / ".jarvis-home-automation"
ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,14}[a-z0-9]$")
MINIMUM_AZURE_CLI_VERSION = (2, 60, 0)
FOUNDRY_DEPLOYMENT_NAME = "gpt-realtime-2"
FOUNDRY_MODEL_NAME = "gpt-realtime-2"
FOUNDRY_MODEL_VERSION = "2026-05-06"
FOUNDRY_DEPLOYMENT_SKU = "GlobalStandard"
PROVIDERS = (
    "Microsoft.Storage",
    "Microsoft.Web",
    "Microsoft.OperationalInsights",
    "Microsoft.Insights",
    "Microsoft.AlertsManagement",
    "Microsoft.CognitiveServices",
)


class LifecycleError(RuntimeError):
    """Raised when an Azure lifecycle operation cannot complete safely."""


def _run(
    command: Sequence[str],
    *,
    capture: bool = False,
    check: bool = True,
    sensitive: frozenset[int] = frozenset(),
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=SOURCE_ROOT,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and result.returncode:
        rendered = " ".join(
            "******" if index in sensitive else item
            for index, item in enumerate(command)
        )
        detail = (result.stderr or result.stdout or "").strip()
        raise LifecycleError(
            f"Command failed: {rendered}" + (f"\n{detail}" if detail else "")
        )
    return result


def _require_azure_cli() -> str:
    command = os.environ.get("AZ_COMMAND", "az")
    resolved_command = shutil.which(command)
    if resolved_command is None:
        raise LifecycleError(
            "Azure CLI was not found. Install it from "
            "https://learn.microsoft.com/cli/azure/install-azure-cli"
        )
    if _run(
        [resolved_command, "account", "show", "--output", "none"],
        check=False,
    ).returncode:
        raise LifecycleError("Azure CLI is not signed in. Run `az login` first.")
    result = _run([resolved_command, "version", "--output", "json"], capture=True)
    try:
        payload = json.loads(result.stdout)
        version_text = str(payload["azure-cli"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LifecycleError("Azure CLI returned an invalid version response.") from exc
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version_text)
    if match is None:
        raise LifecycleError(
            f"Azure CLI returned an unsupported version value: {version_text!r}."
        )
    version = tuple(int(part) for part in match.groups())
    if version < MINIMUM_AZURE_CLI_VERSION:
        required = ".".join(str(part) for part in MINIMUM_AZURE_CLI_VERSION)
        raise LifecycleError(
            f"Azure CLI {required} or newer is required for Flex Consumption; "
            f"found {version_text}. Upgrade Azure CLI and retry."
        )
    return resolved_command


def _validate_environment(name: str) -> None:
    if not ENVIRONMENT_PATTERN.fullmatch(name):
        raise LifecycleError(
            "Environment name must be 2-16 lowercase letters, numbers, or "
            "hyphens and must start and end with a letter or number."
        )


def _canonical_guid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise LifecycleError("--device-guid must be a canonical lowercase UUID.") from exc
    canonical = str(parsed)
    if value != canonical:
        raise LifecycleError(f"--device-guid must use canonical form: {canonical}")
    return canonical


def _state_path(environment: str) -> Path:
    return STATE_ROOT / f"{environment}.json"


def _load_state(environment: str) -> dict[str, Any]:
    path = _state_path(environment)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LifecycleError(f"Could not read lifecycle state {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LifecycleError(f"Lifecycle state {path} is not a JSON object.")
    return value


def _save_state(environment: str, state: dict[str, Any]) -> None:
    STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = _state_path(environment)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def _select_subscription(az: str, subscription_id: str | None) -> str:
    if subscription_id:
        _run([az, "account", "set", "--subscription", subscription_id])
    result = _run(
        [az, "account", "show", "--query", "id", "--output", "tsv"],
        capture=True,
    )
    selected = result.stdout.strip()
    if not selected:
        raise LifecycleError("Azure CLI did not return a subscription ID.")
    return selected


def _group_exists(az: str, name: str) -> bool:
    result = _run(
        [az, "group", "exists", "--name", name],
        capture=True,
    )
    return result.stdout.strip().lower() == "true"


def _recover_existing_values(
    az: str,
    resource_group: str,
) -> tuple[str, str]:
    seed = _run(
        [
            az,
            "group",
            "show",
            "--name",
            resource_group,
            "--query",
            "tags.jarvisResourceNameSeed",
            "--output",
            "tsv",
        ],
        capture=True,
    ).stdout.strip()
    function_name = _run(
        [
            az,
            "functionapp",
            "list",
            "--resource-group",
            resource_group,
            "--query",
            "[0].name",
            "--output",
            "tsv",
        ],
        capture=True,
    ).stdout.strip()
    device_guid = ""
    if function_name:
        device_guid = _run(
            [
                az,
                "functionapp",
                "config",
                "appsettings",
                "list",
                "--resource-group",
                resource_group,
                "--name",
                function_name,
                "--query",
                "[?name=='DEVICE_GUID'].value | [0]",
                "--output",
                "tsv",
            ],
            capture=True,
        ).stdout.strip()
    return seed, device_guid


def _register_providers(az: str) -> None:
    for namespace in PROVIDERS:
        state = _run(
            [
                az,
                "provider",
                "show",
                "--namespace",
                namespace,
                "--query",
                "registrationState",
                "--output",
                "tsv",
            ],
            capture=True,
        ).stdout.strip()
        if state.lower() == "registered":
            print(f"Provider is already registered: {namespace}")
            continue
        print(f"Registering provider: {namespace}")
        _run([az, "provider", "register", "--namespace", namespace, "--wait"])
        state = _run(
            [
                az,
                "provider",
                "show",
                "--namespace",
                namespace,
                "--query",
                "registrationState",
                "--output",
                "tsv",
            ],
            capture=True,
        ).stdout.strip()
        if state.lower() != "registered":
            raise LifecycleError(
                f"Provider {namespace} did not reach Registered state; "
                f"current state: {state or 'unknown'}."
            )


def _validate_flex_location(az: str, location: str) -> None:
    result = _run(
        [
            az,
            "functionapp",
            "list-flexconsumption-locations",
            "--query",
            "[].name",
            "--output",
            "json",
        ],
        capture=True,
    )
    try:
        values = json.loads(result.stdout)
    except ValueError as exc:
        raise LifecycleError(
            "Azure CLI returned invalid Flex Consumption location data."
        ) from exc
    supported = (
        {
            str(value).strip().lower()
            for value in values
            if isinstance(value, str) and value.strip()
        }
        if isinstance(values, list)
        else set()
    )
    if location.lower() not in supported:
        raise LifecycleError(
            f"Flex Consumption is not available in {location!r}. Run "
            "`az functionapp list-flexconsumption-locations --output table` "
            "and choose a supported --location."
        )
    print(f"Flex Consumption location is available: {location}")


def _validate_foundry_model(az: str, location: str) -> None:
    result = _run(
        [
            az,
            "cognitiveservices",
            "model",
            "list",
            "--location",
            location,
            "--output",
            "json",
        ],
        capture=True,
    )
    try:
        values = json.loads(result.stdout)
    except ValueError as exc:
        raise LifecycleError("Azure CLI returned invalid Foundry model data.") from exc
    if not isinstance(values, list):
        raise LifecycleError("Azure CLI returned invalid Foundry model data.")

    for value in values:
        if not isinstance(value, dict):
            continue
        model = value.get("model")
        if not isinstance(model, dict):
            continue
        if (
            model.get("name") != FOUNDRY_MODEL_NAME
            or model.get("version") != FOUNDRY_MODEL_VERSION
        ):
            continue
        skus = model.get("skus")
        if isinstance(skus, list) and any(
            isinstance(sku, dict) and sku.get("name") == FOUNDRY_DEPLOYMENT_SKU
            for sku in skus
        ):
            print(
                "Foundry model is available: "
                f"{FOUNDRY_MODEL_NAME} {FOUNDRY_MODEL_VERSION} "
                f"({FOUNDRY_DEPLOYMENT_SKU}) in {location}"
            )
            return
    raise LifecycleError(
        f"{FOUNDRY_MODEL_NAME} {FOUNDRY_MODEL_VERSION} with "
        f"{FOUNDRY_DEPLOYMENT_SKU} is not available in {location!r}. "
        "Choose a supported --foundry-location."
    )


def _deployment_outputs(
    az: str,
    *,
    environment: str,
    location: str,
    foundry_location: str,
    resource_name_seed: str,
    device_guid: str,
) -> dict[str, str]:
    command = [
        az,
        "deployment",
        "sub",
        "create",
        "--name",
        f"jarvis-{environment}",
        "--location",
        location,
        "--template-file",
        str(INFRA_ROOT / "main.bicep"),
        "--parameters",
        f"environmentName={environment}",
        f"resourceNameSeed={resource_name_seed}",
        f"location={location}",
        f"foundryLocation={foundry_location}",
        f"deviceGuid={device_guid}",
        f"foundryDeploymentName={FOUNDRY_DEPLOYMENT_NAME}",
        f"foundryModelName={FOUNDRY_MODEL_NAME}",
        f"foundryModelVersion={FOUNDRY_MODEL_VERSION}",
        "--query",
        "properties.outputs",
        "--output",
        "json",
    ]
    result = _run(
        command,
        capture=True,
        sensitive=frozenset({command.index(f"deviceGuid={device_guid}")}),
    )
    try:
        raw = json.loads(result.stdout)
        return {name: str(item["value"]) for name, item in raw.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise LifecycleError("Azure deployment returned invalid outputs.") from exc


def _create_backend_zip(destination: Path) -> None:
    excluded_parts = {"__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "tests"}
    excluded_names = {"local.settings.json", "local.settings.example.json", "README.md"}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(BACKEND_ROOT.rglob("*")):
            relative = path.relative_to(BACKEND_ROOT)
            if not path.is_file():
                continue
            if any(part in excluded_parts or part.startswith(".") for part in relative.parts):
                continue
            if path.name in excluded_names or path.suffix == ".pyc":
                continue
            archive.write(path, relative.as_posix())


def _deploy_backend_code(
    az: str,
    resource_group: str,
    function_name: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="jarvis-backend-") as temporary_dir:
        package = Path(temporary_dir) / "backend.zip"
        _create_backend_zip(package)
        command = [
            az,
            "functionapp",
            "deployment",
            "source",
            "config-zip",
            "--resource-group",
            resource_group,
            "--name",
            function_name,
            "--src",
            str(package),
            "--build-remote",
            "true",
            "--timeout",
            "1200",
            "--output",
            "none",
        ]
        last_detail = ""
        for attempt in range(1, 13):
            print(f"Deploying backend code (attempt {attempt}/12)...")
            result = _run(command, capture=True, check=False)
            if result.returncode == 0:
                return
            last_detail = (result.stderr or result.stdout or "").strip()
            if attempt < 12:
                print("Deployment identity is not ready yet; retrying in 15 seconds.")
                time.sleep(15)
        raise LifecycleError(
            "Backend code deployment failed after RBAC propagation retries."
            + (f"\n{last_detail}" if last_detail else "")
        )


def _wait_for_health(api_base_url: str) -> None:
    health_url = f"{api_base_url.rstrip('/')}/health"
    last_error = ""
    for _ in range(60):
        try:
            with urllib.request.urlopen(health_url, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload == {"status": "ok"}:
                    print(f"Backend health check passed: {health_url}")
                    return
                last_error = f"HTTP {response.status}: {payload!r}"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(5)
    raise LifecycleError(
        f"Backend did not become healthy at {health_url}: "
        f"{last_error or 'no response'}"
    )


def _find_foundry_account(az: str, resource_group: str) -> tuple[str, str]:
    result = _run(
        [
            az,
            "cognitiveservices",
            "account",
            "list",
            "--resource-group",
            resource_group,
            "--query",
            "[?kind=='AIServices'] | [0].{name:name,location:location}",
            "--output",
            "json",
        ],
        capture=True,
    )
    if not result.stdout.strip():
        return "", ""
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise LifecycleError(
            "Azure CLI returned invalid JSON while locating the Foundry account."
        ) from exc
    if not isinstance(value, dict):
        return "", ""
    return str(value.get("name", "")), str(value.get("location", ""))


def _purge_foundry_account(
    az: str,
    *,
    name: str,
    resource_group: str,
    location: str,
) -> None:
    if not name or not location:
        return
    result = _run(
        [
            az,
            "cognitiveservices",
            "account",
            "purge",
            "--name",
            name,
            "--resource-group",
            resource_group,
            "--location",
            location,
        ],
        capture=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        if "not found" in detail.lower() or "resourcenotfound" in detail.lower():
            return
        raise LifecycleError(
            f"Foundry soft-delete purge failed for {name}."
            + (f"\n{detail}" if detail else "")
        )


def install_backend(args: argparse.Namespace) -> None:
    _validate_environment(args.environment_name)
    az = _require_azure_cli()
    subscription_id = _select_subscription(az, args.subscription_id)
    resource_group = f"rg-{args.environment_name}-jarvis"
    state = _load_state(args.environment_name)

    recovered_seed = ""
    recovered_guid = ""
    if _group_exists(az, resource_group):
        recovered_seed, recovered_guid = _recover_existing_values(az, resource_group)

    device_guid = (
        _canonical_guid(args.device_guid)
        if args.device_guid
        else recovered_guid or str(state.get("device_guid", "")) or str(uuid.uuid4())
    )
    device_guid = _canonical_guid(device_guid)
    resource_name_seed = (
        recovered_seed
        or str(state.get("resource_name_seed", ""))
        or secrets.token_hex(8)
    )
    state.update(
        {
            "environment_name": args.environment_name,
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "location": args.location,
            "foundry_location": args.foundry_location,
            "device_guid": device_guid,
            "resource_name_seed": resource_name_seed,
            "uninstalled": False,
        }
    )
    _save_state(args.environment_name, state)

    _register_providers(az)
    _validate_flex_location(az, args.location)
    _validate_foundry_model(az, args.foundry_location)
    print(
        f"Creating/updating Azure services in {args.location}; "
        f"Foundry model region: {args.foundry_location}."
    )
    outputs = _deployment_outputs(
        az,
        environment=args.environment_name,
        location=args.location,
        foundry_location=args.foundry_location,
        resource_name_seed=resource_name_seed,
        device_guid=device_guid,
    )
    function_name = outputs.get("functionAppName", "")
    api_base_url = outputs.get("apiBaseUrl", "")
    if not function_name or not api_base_url:
        raise LifecycleError("Deployment did not return the Function App outputs.")

    _deploy_backend_code(az, resource_group, function_name)
    if not args.skip_health_check:
        _wait_for_health(api_base_url)
    state.update(
        {
            "function_app_name": function_name,
            "api_base_url": api_base_url,
            "foundry_account_name": outputs.get("foundryAccountName", ""),
            "foundry_location": args.foundry_location,
        }
    )
    _save_state(args.environment_name, state)

    print("\nAzure backend installation complete.")
    print(f"Resource group : {resource_group}")
    print(f"Function App   : {function_name}")
    print(f"API base URL   : {api_base_url}")
    print(f"Device GUID    : {device_guid}")
    print("\nUse the API base URL and Device GUID in the Pi install command.")


def uninstall_backend(args: argparse.Namespace) -> None:
    _validate_environment(args.environment_name)
    if not args.yes:
        raise LifecycleError("Refusing to delete Azure services without --yes.")
    az = _require_azure_cli()
    subscription_id = _select_subscription(az, args.subscription_id)
    resource_group = args.resource_group or f"rg-{args.environment_name}-jarvis"
    state = _load_state(args.environment_name)
    foundry_name = str(state.get("foundry_account_name", ""))
    foundry_location = str(state.get("foundry_location", ""))

    if _group_exists(az, resource_group):
        discovered_name, discovered_location = _find_foundry_account(
            az, resource_group
        )
        foundry_name = discovered_name or foundry_name
        foundry_location = discovered_location or foundry_location
        print(f"Deleting resource group {resource_group} and every contained service...")
        _run([az, "group", "delete", "--name", resource_group, "--yes"])
        if _group_exists(az, resource_group):
            raise LifecycleError(f"Azure still reports resource group {resource_group}.")
    else:
        print(f"Resource group {resource_group} is already absent.")

    _purge_foundry_account(
        az,
        name=foundry_name,
        resource_group=resource_group,
        location=foundry_location,
    )
    if not state.get("uninstalled", False):
        state["resource_name_seed"] = secrets.token_hex(8)
    state.update(
        {
            "environment_name": args.environment_name,
            "subscription_id": subscription_id,
            "resource_group": resource_group,
            "uninstalled": True,
        }
    )
    _save_state(args.environment_name, state)
    print("Azure backend and all of its services are removed.")
    print(f"Pi identity retained locally in {_state_path(args.environment_name)}.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or uninstall the complete Jarvis Azure backend."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    install_parser = subparsers.add_parser(
        "install",
        help="create/update services, deploy code, and check health",
    )
    install_parser.add_argument("-e", "--environment-name", required=True)
    install_parser.add_argument("--subscription-id")
    install_parser.add_argument("--location", default="australiaeast")
    install_parser.add_argument("--foundry-location", default="southindia")
    install_parser.add_argument("--device-guid")
    install_parser.add_argument("--skip-health-check", action="store_true")

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="delete the resource group and every backend service",
    )
    uninstall_parser.add_argument("-e", "--environment-name", required=True)
    uninstall_parser.add_argument("--subscription-id")
    uninstall_parser.add_argument("--resource-group")
    uninstall_parser.add_argument("--yes", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.action == "install":
            install_backend(args)
        else:
            uninstall_backend(args)
    except LifecycleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
