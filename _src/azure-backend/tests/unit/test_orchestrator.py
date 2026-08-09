from __future__ import annotations

import pytest

from home_assistant_api.ai.orchestrator import AssistantOrchestrator
from home_assistant_api.errors import UpstreamServiceError
from home_assistant_api.repositories.reminders import InMemoryRemindersRepository
from home_assistant_api.repositories.todos import InMemoryTodosRepository
from home_assistant_api.tools import ToolContext

from tests.conftest import FakeChatClient, FakeToolCall, text_response, tool_call_response


def _tool_context() -> ToolContext:
    return ToolContext(
        device_id="device-1",
        todos_repo=InMemoryTodosRepository(),
        reminders_repo=InMemoryRemindersRepository(),
        credential_store=None,
    )


def test_run_turn_returns_text_reply_with_no_tool_calls():
    chat_client = FakeChatClient([text_response("Hello there!")])
    orchestrator = AssistantOrchestrator(chat_client=chat_client, deployment="test-deployment")
    result = orchestrator.run_turn(
        system_prompt="You are a helpful assistant.",
        history=[],
        user_text="Hi",
        tool_context=_tool_context(),
    )
    assert result.reply_text == "Hello there!"
    assert result.actions == []
    assert result.finish_reason == "stop"
    # user message + final assistant message
    assert len(result.new_messages) == 2


def test_run_turn_executes_single_tool_call_then_returns_reply():
    chat_client = FakeChatClient(
        [
            tool_call_response(
                [FakeToolCall("call-1", "create_todo", '{"title": "Buy milk"}')]
            ),
            text_response("I've added that to your todo list."),
        ]
    )
    orchestrator = AssistantOrchestrator(chat_client=chat_client, deployment="test-deployment")
    result = orchestrator.run_turn(
        system_prompt="You are a helpful assistant.",
        history=[],
        user_text="Add buy milk to my todos",
        tool_context=_tool_context(),
    )
    assert result.reply_text == "I've added that to your todo list."
    assert len(result.actions) == 1
    assert result.actions[0].type == "create_todo"
    assert result.actions[0].status == "completed"


def test_run_turn_handles_multiple_tool_iterations():
    chat_client = FakeChatClient(
        [
            tool_call_response([FakeToolCall("call-1", "create_todo", '{"title": "Buy milk"}')]),
            tool_call_response([FakeToolCall("call-2", "list_todos", "{}")]),
            text_response("Done - you now have one todo."),
        ]
    )
    orchestrator = AssistantOrchestrator(chat_client=chat_client, deployment="test-deployment", max_iterations=5)
    result = orchestrator.run_turn(
        system_prompt="You are a helpful assistant.",
        history=[],
        user_text="Add buy milk then list my todos",
        tool_context=_tool_context(),
    )
    assert result.reply_text == "Done - you now have one todo."
    assert len(result.actions) == 2
    assert [a.type for a in result.actions] == ["create_todo", "list_todos"]


def test_run_turn_failed_tool_call_produces_failed_action_but_continues():
    chat_client = FakeChatClient(
        [
            tool_call_response([FakeToolCall("call-1", "complete_todo", '{"todo_id": "missing"}')]),
            text_response("Sorry, I couldn't find that todo."),
        ]
    )
    orchestrator = AssistantOrchestrator(chat_client=chat_client, deployment="test-deployment")
    result = orchestrator.run_turn(
        system_prompt="You are a helpful assistant.",
        history=[],
        user_text="Complete the missing todo",
        tool_context=_tool_context(),
    )
    assert result.actions[0].status == "failed"
    assert result.reply_text == "Sorry, I couldn't find that todo."


def test_run_turn_exceeding_max_iterations_raises_upstream_error():
    chat_client = FakeChatClient(
        [
            tool_call_response([FakeToolCall("call-1", "list_todos", "{}")]),
            tool_call_response([FakeToolCall("call-2", "list_todos", "{}")]),
        ]
    )
    orchestrator = AssistantOrchestrator(chat_client=chat_client, deployment="test-deployment", max_iterations=2)
    with pytest.raises(UpstreamServiceError):
        orchestrator.run_turn(
            system_prompt="You are a helpful assistant.",
            history=[],
            user_text="Keep listing my todos forever",
            tool_context=_tool_context(),
        )


def test_run_turn_chat_client_exception_raises_upstream_error():
    class _BoomChatClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("network exploded")

    orchestrator = AssistantOrchestrator(chat_client=_BoomChatClient(), deployment="test-deployment")
    with pytest.raises(UpstreamServiceError):
        orchestrator.run_turn(
            system_prompt="You are a helpful assistant.",
            history=[],
            user_text="Hi",
            tool_context=_tool_context(),
        )
