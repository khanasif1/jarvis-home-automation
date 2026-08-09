"""Tool implementations invoked by the assistant orchestrator.

Every tool function takes a :class:`ToolContext` plus a ``dict`` of
arguments (already validated against the tool's JSON schema by
``ai/tool_executor.py``) and returns a plain ``dict`` suitable for feeding
back to the model as a tool result. Tools raise the specific
:mod:`home_assistant_api.errors` type that applies; they never swallow
errors to fabricate a successful-looking result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from google.oauth2.credentials import Credentials

from home_assistant_api.google.calendar_client import CalendarService, build_calendar_service
from home_assistant_api.google.credentials import CredentialStore
from home_assistant_api.google.gmail_client import GmailService, build_gmail_service
from home_assistant_api.google.tasks_client import TasksService, build_tasks_service
from home_assistant_api.repositories.reminders import RemindersRepository
from home_assistant_api.repositories.todos import TodosRepository


@dataclass
class ToolContext:
    """Everything a tool needs to act on behalf of one device/turn."""

    device_id: str
    todos_repo: TodosRepository
    reminders_repo: RemindersRepository
    credential_store: Optional[CredentialStore] = None
    calendar_service_factory: Callable[[Credentials], CalendarService] = build_calendar_service
    tasks_service_factory: Callable[[Credentials], TasksService] = build_tasks_service
    gmail_service_factory: Callable[[Credentials], GmailService] = build_gmail_service
