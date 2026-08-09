from __future__ import annotations

import json

from home_assistant_api import routes

from tests.integration.helpers import make_request


def test_health_returns_exact_contract_shape(app_context_factory, base_env):
    ctx = app_context_factory(base_env)
    req = make_request(method="GET", url="http://localhost/api/health")
    response = routes.health(req, ctx)
    assert response.status_code == 200
    assert json.loads(response.get_body()) == {"status": "ok"}


def test_health_does_not_require_any_optional_configuration(app_context_factory, base_env):
    # base_env has none of DEVICE_API_TOKENS / AZURE_OPENAI_* / SPEECH_* / GOOGLE_* set.
    ctx = app_context_factory(base_env)
    req = make_request(method="GET", url="http://localhost/api/health")
    response = routes.health(req, ctx)
    assert response.status_code == 200
