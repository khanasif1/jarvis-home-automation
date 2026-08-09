"""Deployment-packaging validation.

These tests protect two independent requirements:

1. A deployed Azure Functions worker imports ``function_app.py`` directly by
   path -- it is never installed as a package, never run through pytest,
   and has no editable install or ``PYTHONPATH`` set up for it. The
   ``sys.path`` bootstrap at the top of ``function_app.py`` must make
   ``import home_assistant_api`` work anyway. We prove this by importing it
   in a *subprocess* with ``PYTHONPATH`` explicitly unset, so pytest's own
   ``pythonpath = src`` ini option (see ``pytest.ini``) cannot mask a
   regression.

2. The deployment package (whatever ``.funcignore`` allows through) must
   contain only backend runtime code, production dependencies, prompts, and
   generated API models -- never tests, dev dependencies, or anything from
   ``pi-client``/``infra``. We prove this by copying the repository into a
   scratch directory under root ``.test-artifacts/``, applying the same
   ignore rules ``func azure functionapp publish`` would apply, and
   asserting the result is clean.
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_ROOT.parent
_ARTIFACTS_ROOT = _REPO_ROOT / ".test-artifacts" / "azure-backend-deployment-package"


def test_function_app_imports_without_pythonpath_in_subprocess():
    """Simulate the Azure Functions worker's import of function_app.py.

    A bare ``python -c "import function_app"`` run from the backend root,
    in a fresh subprocess with ``PYTHONPATH`` removed from the environment,
    is the closest local approximation of how the Functions host loads this
    module: no pytest ini options, no editable install, no inherited
    ``PYTHONPATH``. If the ``sys.path`` bootstrap in ``function_app.py``
    were missing or broken, this would fail with ``ModuleNotFoundError``.
    """

    env = {
        key: value
        for key, value in __import__("os").environ.items()
        if key != "PYTHONPATH"
    }
    # function_app.py builds a default AppContext at import time; give it a
    # minimal, fully-unconfigured development environment so import succeeds
    # without reaching out to any real dependency.
    env["APP_ENVIRONMENT"] = "development"

    result = subprocess.run(
        [sys.executable, "-c", "import function_app; print('IMPORT_OK')"],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"function_app import failed without PYTHONPATH.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "IMPORT_OK" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_function_app_has_unique_http_routes():
    """Public routes stay unique and out of the host-reserved admin namespace."""
    import function_app

    host_config = json.loads((_BACKEND_ROOT / "host.json").read_text(encoding="utf-8"))
    assert host_config["extensions"]["http"]["routePrefix"] == ""

    route_methods: dict[str, set[str]] = {}
    for function in function_app.app.get_functions():
        metadata = json.loads(function.get_function_json())
        trigger = next(
            binding
            for binding in metadata["bindings"]
            if binding["type"] == "httpTrigger"
        )
        route = trigger["route"]
        assert route.startswith("api/")
        assert route not in route_methods, (
            f"duplicate HTTP route {route!r}; combine its methods in one function"
        )
        route_methods[route] = set(trigger["methods"])

    assert route_methods["api/admin/devices"] == {"GET", "POST"}


@pytest.mark.parametrize(
    ("method", "handler_name"),
    [("GET", "list_devices"), ("POST", "register_device")],
)
def test_admin_devices_dispatches_by_method(monkeypatch, method, handler_name):
    import function_app

    request = type("Request", (), {"method": method})()
    expected_response = object()

    def handler(received_request, received_context):
        assert received_request is request
        assert received_context is function_app._context
        return expected_response

    monkeypatch.setattr(function_app.routes, handler_name, handler)
    assert function_app.admin_devices(request) is expected_response


def _read_funcignore_patterns() -> list[str]:
    funcignore = _BACKEND_ROOT / ".funcignore"
    patterns: list[str] = []
    for line in funcignore.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def _is_ignored(relative_posix: str, name: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        candidate = pattern.rstrip("/")
        if fnmatch.fnmatch(name, candidate):
            return True
        if fnmatch.fnmatch(relative_posix, candidate):
            return True
        if pattern.endswith("/") and (
            relative_posix == candidate or relative_posix.startswith(candidate + "/")
        ):
            return True
    return False


@pytest.fixture()
def deployment_package_copy():
    """Copy azure-backend into .test-artifacts, applying .funcignore rules.

    This mirrors (a conservative superset of) what the Azure Functions Core
    Tools packager does: everything is included except paths matched by
    ``.funcignore``. We do not depend on Core Tools being installed locally,
    so this reimplements the filtering directly against the same
    ``.funcignore`` file the real deployment uses.
    """

    patterns = _read_funcignore_patterns()
    dest = _ARTIFACTS_ROOT / "package"
    if dest.exists():
        import shutil

        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    import shutil

    for source_path in _BACKEND_ROOT.rglob("*"):
        relative = source_path.relative_to(_BACKEND_ROOT)
        relative_posix = relative.as_posix()
        if _is_ignored(relative_posix, source_path.name, patterns):
            continue
        target = dest / relative
        if source_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)

    yield dest


def test_deployment_package_excludes_tests_and_dev_dependencies(
    deployment_package_copy: Path,
):
    package_files = {
        p.relative_to(deployment_package_copy).as_posix()
        for p in deployment_package_copy.rglob("*")
        if p.is_file()
    }

    forbidden_paths = [
        "tests",
        "requirements-dev.txt",
        "pytest.ini",
        "local.settings.json",
        "local.settings.example.json",
        "README.md",
    ]
    for forbidden in forbidden_paths:
        assert not any(
            f == forbidden or f.startswith(forbidden + "/") for f in package_files
        ), f"deployment package unexpectedly contains {forbidden!r}: {sorted(package_files)}"

    for f in package_files:
        assert "__pycache__" not in f, f
        assert not f.endswith(".pyc"), f
        assert "pi-client" not in f, f
        assert "/infra/" not in f and not f.startswith("infra/"), f
        assert "/tests/" not in f, f


def test_deployment_package_contains_required_runtime_files(
    deployment_package_copy: Path,
):
    package_files = {
        p.relative_to(deployment_package_copy).as_posix()
        for p in deployment_package_copy.rglob("*")
        if p.is_file()
    }

    assert "function_app.py" in package_files
    assert "host.json" in package_files
    assert "requirements.txt" in package_files
    assert any(f.startswith("src/home_assistant_api/") for f in package_files)
    assert any(f.startswith("prompts/") for f in package_files)


def test_deployment_package_is_importable_standalone():
    """The package copy (with tests/dev-deps stripped) must still import,
    proving the runtime code has no hidden dependency on anything
    ``.funcignore`` strips out."""

    patterns = _read_funcignore_patterns()
    dest = _ARTIFACTS_ROOT / "package-import-check"
    import shutil

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    for source_path in _BACKEND_ROOT.rglob("*"):
        relative = source_path.relative_to(_BACKEND_ROOT)
        relative_posix = relative.as_posix()
        if _is_ignored(relative_posix, source_path.name, patterns):
            continue
        target = dest / relative
        if source_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)

    import os

    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["APP_ENVIRONMENT"] = "development"

    result = subprocess.run(
        [sys.executable, "-c", "import function_app; print('IMPORT_OK')"],
        cwd=str(dest),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"stripped deployment package failed to import.\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "IMPORT_OK" in result.stdout
