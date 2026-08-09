"""Shared pytest fixtures and lightweight test doubles.

None of these fixtures perform network I/O or require cloud credentials --
they build the same in-memory repositories and explicit fakes the backend
already exposes for local development.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

import pytest

from home_assistant_api.app_context import AppContext
from home_assistant_api.config import AppConfig

# ---------------------------------------------------------------------------
# Scratch directory for tests that need to write files. Always kept under
# the source root's .test-artifacts/, never system temp directories.
# ---------------------------------------------------------------------------

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_SCRATCH_ROOT = _SOURCE_ROOT / ".test-artifacts" / "azure-backend-tests"


@pytest.fixture()
def scratch_dir(request: pytest.FixtureRequest) -> Path:
    directory = _SCRATCH_ROOT / request.node.name.replace("/", "_")
    directory.mkdir(parents=True, exist_ok=True)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture()
def base_env() -> dict[str, str]:
    """A minimal environment with every optional dependency unconfigured."""

    return {"APP_ENVIRONMENT": "development"}


@pytest.fixture()
def full_env(base_env: dict[str, str]) -> dict[str, str]:
    """An environment with every dependency configured, for happy-path tests."""

    env = dict(base_env)
    env.update(
        {
            "DEVICE_API_TOKENS": '{"device-one": "device-one-token-0123456789"}',
            "ADMIN_API_KEY": "admin-key-0123456789",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_API_KEY": "fake-aoai-key",
            "AZURE_OPENAI_DEPLOYMENT": "test-deployment",
            "SPEECH_REGION": "eastus",
            "SPEECH_API_KEY": "fake-speech-key",
            "GOOGLE_OAUTH_CLIENT_ID": "fake-client-id",
            "GOOGLE_OAUTH_CLIENT_SECRET": "fake-client-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://example.azurewebsites.net/api/google/oauth/callback",
        }
    )
    return env


def make_config(env: dict[str, str]) -> AppConfig:
    return AppConfig.from_environment(env)


@pytest.fixture()
def app_context_factory():
    def _factory(env: dict[str, str], **overrides: Any) -> AppContext:
        config = make_config(env)
        return AppContext(config, seed_devices_from_config=True, **overrides)

    return _factory


# ---------------------------------------------------------------------------
# Fake Azure OpenAI chat client (structural match for openai.AzureOpenAI)
# ---------------------------------------------------------------------------


class FakeFunctionCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = FakeFunctionCall(name, arguments)


class FakeMessage:
    def __init__(self, content: Optional[str] = None, tool_calls: Optional[list] = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message: FakeMessage, finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason


class FakeChatResponse:
    def __init__(self, choices: list[FakeChoice], model: str = "fake-model") -> None:
        self.choices = choices
        self.model = model


class FakeChatCompletions:
    def __init__(self, responses: list[FakeChatResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeChatResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeChatCompletions ran out of queued responses")
        return self._responses.pop(0)


class FakeChat:
    def __init__(self, completions: FakeChatCompletions) -> None:
        self.completions = completions


class FakeChatClient:
    """Structural fake for ``openai.AzureOpenAI`` used by the orchestrator."""

    def __init__(self, responses: list[FakeChatResponse]) -> None:
        self.chat = FakeChat(FakeChatCompletions(responses))


def text_response(text: str, *, finish_reason: str = "stop", model: str = "fake-model") -> FakeChatResponse:
    return FakeChatResponse(
        choices=[FakeChoice(FakeMessage(content=text), finish_reason=finish_reason)],
        model=model,
    )


def tool_call_response(tool_calls: list[FakeToolCall], *, model: str = "fake-model") -> FakeChatResponse:
    return FakeChatResponse(
        choices=[FakeChoice(FakeMessage(content=None, tool_calls=tool_calls), finish_reason="tool_calls")],
        model=model,
    )
