"""Azure Functions v2 (Python programming model) entry point.

This module only wires HTTP routes to their handlers in
``home_assistant_api.routes``; all logic lives in the ``home_assistant_api``
package so it can be unit/integration tested without a running Functions
host. Nothing here imports ``pi-client``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The Azure Functions Python worker imports this file directly by path (it
# is not installed as a package, and is not run through pytest, so neither
# an editable install nor pytest's ``pythonpath = src`` ini option applies
# in a deployed Function App). Without this bootstrap, `import
# home_assistant_api` below would fail with ``ModuleNotFoundError`` the
# moment this file is loaded on a fresh worker. Prepending the deployed
# ``src`` directory (a sibling of this file, always deployed alongside it --
# see .funcignore) makes the package importable regardless of PYTHONPATH,
# working directory, or whether the package was pip-installed.
_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import azure.functions as func

from home_assistant_api.app_context import build_default_context
from home_assistant_api import routes

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Built once per worker process. Configuration is validated lazily per
# dependency (see AppConfig), so importing this module never fails even if
# optional integrations (Google, Speech, Azure OpenAI) are not configured.
_context = build_default_context()


@app.route(route="api/health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return routes.health(req, _context)


@app.route(route="api/voice-turn", methods=["POST"])
def voice_turn(req: func.HttpRequest) -> func.HttpResponse:
    return routes.voice_turn(req, _context)


@app.route(route="api/admin/devices", methods=["GET", "POST"])
def admin_devices(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "POST":
        return routes.register_device(req, _context)
    return routes.list_devices(req, _context)


@app.route(route="api/reminders/due", methods=["GET"])
def list_due_reminders(req: func.HttpRequest) -> func.HttpResponse:
    return routes.list_due_reminders(req, _context)


@app.route(route="api/reminders/{reminder_id}/ack", methods=["POST"])
def acknowledge_reminder(req: func.HttpRequest) -> func.HttpResponse:
    return routes.acknowledge_reminder(req, _context)


@app.route(route="api/google/oauth/start", methods=["GET"])
def google_oauth_start(req: func.HttpRequest) -> func.HttpResponse:
    return routes.google_oauth_start(req, _context)


@app.route(route="api/google/oauth/callback", methods=["GET"])
def google_oauth_callback(req: func.HttpRequest) -> func.HttpResponse:
    return routes.google_oauth_callback(req, _context)
