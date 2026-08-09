from __future__ import annotations

from types import SimpleNamespace

import pytest

from home_assistant_api.config import GoogleOAuthConfig
from home_assistant_api.errors import ConfigurationError, UpstreamServiceError
from home_assistant_api.google.calendar_client import GoogleCalendarClient
from home_assistant_api.google.credentials import CredentialStore
from home_assistant_api.google.gmail_client import GmailClient
from home_assistant_api.google.oauth import require_oauth_client
from home_assistant_api.google.tasks_client import GoogleTasksClient
from googleapiclient.errors import HttpError


def _http_error(status: int) -> HttpError:
    response = SimpleNamespace(status=status, reason="error")
    return HttpError(response, b"{}")


class _FakeExecutable:
    def __init__(self, result=None, exception: Exception | None = None):
        self._result = result
        self._exception = exception

    def execute(self):
        if self._exception is not None:
            raise self._exception
        return self._result


class _FakeEvents:
    def __init__(self, executable: _FakeExecutable):
        self._executable = executable

    def list(self, **kwargs):
        return self._executable

    def insert(self, **kwargs):
        return self._executable


class _FakeCalendarService:
    def __init__(self, executable: _FakeExecutable):
        self._executable = executable

    def events(self):
        return _FakeEvents(self._executable)


class _FakeTasks:
    def __init__(self, executable: _FakeExecutable):
        self._executable = executable

    def list(self, **kwargs):
        return self._executable

    def insert(self, **kwargs):
        return self._executable

    def patch(self, **kwargs):
        return self._executable


class _FakeTasksService:
    def __init__(self, executable: _FakeExecutable):
        self._executable = executable

    def tasks(self):
        return _FakeTasks(self._executable)


class _FakeMessages:
    def __init__(self, executable: _FakeExecutable):
        self._executable = executable

    def list(self, **kwargs):
        return self._executable

    def get(self, **kwargs):
        return self._executable


class _FakeUsers:
    def __init__(self, executable: _FakeExecutable):
        self._executable = executable

    def messages(self):
        return _FakeMessages(self._executable)


class _FakeGmailService:
    def __init__(self, executable: _FakeExecutable):
        self._executable = executable

    def users(self):
        return _FakeUsers(self._executable)


class TestGoogleOAuth:
    def test_require_oauth_client_raises_when_unconfigured(self):
        with pytest.raises(ConfigurationError):
            require_oauth_client(None)

    def test_require_oauth_client_builds_client_when_configured(self):
        config = GoogleOAuthConfig(
            client_id="client-id",
            client_secret="secret",
            redirect_uri="https://example.com/callback",
            scopes=("scope-a",),
        )
        client = require_oauth_client(config)
        assert client is not None


class TestCredentialStore:
    def test_get_credentials_raises_configuration_error_when_never_connected(self):
        store = CredentialStore()
        with pytest.raises(ConfigurationError):
            store.get_credentials("device-1")

    def test_delete_is_a_no_op_for_unknown_device(self):
        store = CredentialStore()
        store.delete("missing")  # should not raise


class TestGoogleCalendarClient:
    def test_list_upcoming_events_returns_items(self):
        executable = _FakeExecutable(result={"items": [{"id": "evt-1"}]})
        client = GoogleCalendarClient(_FakeCalendarService(executable))
        events = client.list_upcoming_events(time_min_iso="2024-01-01T00:00:00Z")
        assert events == [{"id": "evt-1"}]

    def test_list_upcoming_events_wraps_http_error(self):
        executable = _FakeExecutable(exception=_http_error(500))
        client = GoogleCalendarClient(_FakeCalendarService(executable))
        with pytest.raises(UpstreamServiceError):
            client.list_upcoming_events(time_min_iso="2024-01-01T00:00:00Z")

    def test_create_event_wraps_http_error(self):
        executable = _FakeExecutable(exception=_http_error(400))
        client = GoogleCalendarClient(_FakeCalendarService(executable))
        with pytest.raises(UpstreamServiceError):
            client.create_event(summary="Meeting", start_iso="2024-01-01T00:00:00Z", end_iso="2024-01-01T01:00:00Z")

    def test_create_event_success(self):
        executable = _FakeExecutable(result={"id": "evt-1"})
        client = GoogleCalendarClient(_FakeCalendarService(executable))
        result = client.create_event(summary="Meeting", start_iso="2024-01-01T00:00:00Z", end_iso="2024-01-01T01:00:00Z")
        assert result == {"id": "evt-1"}


class TestGoogleTasksClient:
    def test_list_tasks_returns_items(self):
        executable = _FakeExecutable(result={"items": [{"id": "task-1"}]})
        client = GoogleTasksClient(_FakeTasksService(executable))
        assert client.list_tasks() == [{"id": "task-1"}]

    def test_create_task_wraps_http_error(self):
        executable = _FakeExecutable(exception=_http_error(500))
        client = GoogleTasksClient(_FakeTasksService(executable))
        with pytest.raises(UpstreamServiceError):
            client.create_task(title="Buy milk")

    def test_complete_task_success(self):
        executable = _FakeExecutable(result={"status": "completed"})
        client = GoogleTasksClient(_FakeTasksService(executable))
        assert client.complete_task(task_id="task-1") == {"status": "completed"}

    def test_complete_task_wraps_http_error(self):
        executable = _FakeExecutable(exception=_http_error(404))
        client = GoogleTasksClient(_FakeTasksService(executable))
        with pytest.raises(UpstreamServiceError):
            client.complete_task(task_id="missing")


class TestGmailClient:
    def test_search_messages_returns_items(self):
        executable = _FakeExecutable(result={"messages": [{"id": "msg-1"}]})
        client = GmailClient(_FakeGmailService(executable))
        assert client.search_messages(query="is:unread") == [{"id": "msg-1"}]

    def test_search_messages_wraps_http_error(self):
        executable = _FakeExecutable(exception=_http_error(500))
        client = GmailClient(_FakeGmailService(executable))
        with pytest.raises(UpstreamServiceError):
            client.search_messages(query="is:unread")

    def test_get_message_summary_extracts_headers(self):
        executable = _FakeExecutable(
            result={
                "id": "msg-1",
                "snippet": "hello",
                "payload": {"headers": [{"name": "Subject", "value": "Hi"}, {"name": "From", "value": "a@b.com"}]},
            }
        )
        client = GmailClient(_FakeGmailService(executable))
        summary = client.get_message_summary(message_id="msg-1")
        assert summary == {"id": "msg-1", "snippet": "hello", "subject": "Hi", "from": "a@b.com"}

    def test_get_message_summary_wraps_http_error(self):
        executable = _FakeExecutable(exception=_http_error(404))
        client = GmailClient(_FakeGmailService(executable))
        with pytest.raises(UpstreamServiceError):
            client.get_message_summary(message_id="missing")
