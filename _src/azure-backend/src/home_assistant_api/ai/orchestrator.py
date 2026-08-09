"""Azure OpenAI chat orchestration and the explicit tool-call loop.

The orchestrator is deliberately decoupled from the concrete ``openai``
package: it only requires an object exposing
``.chat.completions.create(...)`` with the standard Chat Completions
response shape (``choices[0].message`` with ``content``/``tool_calls``).
That lets tests supply a lightweight fake without any network access while
production wires in ``openai.AzureOpenAI``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from home_assistant_api.ai.tool_definitions import TOOL_DEFINITIONS
from home_assistant_api.ai.tool_executor import execute_tool_call
from home_assistant_api.errors import UpstreamServiceError
from home_assistant_api.models import VoiceTurnAction
from home_assistant_api.repositories.sessions import SessionMessage
from home_assistant_api.telemetry import TelemetryClient, get_telemetry_client
from home_assistant_api.tools import ToolContext

_MAX_TOOL_RESULT_CHARS = 4000


class ChatCompletionsClient(Protocol):
    """Structural interface matching ``openai.AzureOpenAI``'s chat client."""

    chat: Any


@dataclass
class OrchestratorResult:
    reply_text: str
    actions: list[VoiceTurnAction]
    new_messages: list[SessionMessage]
    model: str
    finish_reason: str


@dataclass
class AssistantOrchestrator:
    chat_client: ChatCompletionsClient
    deployment: str
    max_iterations: int = 5
    telemetry: TelemetryClient = field(default_factory=get_telemetry_client)

    def run_turn(
        self,
        *,
        system_prompt: str,
        history: list[SessionMessage],
        user_text: str,
        tool_context: ToolContext,
    ) -> OrchestratorResult:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for entry in history:
            messages.append(_session_message_to_wire(entry))

        user_message = SessionMessage(role="user", content=user_text)
        messages.append(_session_message_to_wire(user_message))

        new_messages: list[SessionMessage] = [user_message]
        actions: list[VoiceTurnAction] = []

        model_name = self.deployment
        finish_reason = "stop"

        for _ in range(self.max_iterations):
            try:
                response = self.chat_client.chat.completions.create(
                    model=self.deployment,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                )
            except Exception as exc:  # narrowed: any transport/SDK failure means "upstream down"
                raise UpstreamServiceError("Azure OpenAI request failed.") from exc

            model_name = getattr(response, "model", self.deployment) or self.deployment
            choice = response.choices[0]
            message = choice.message
            finish_reason = getattr(choice, "finish_reason", "stop") or "stop"
            tool_calls = getattr(message, "tool_calls", None)

            if tool_calls:
                assistant_msg = SessionMessage(
                    role="assistant",
                    content=message.content,
                    tool_calls=[
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                )
                messages.append(_session_message_to_wire(assistant_msg))
                new_messages.append(assistant_msg)

                for call in tool_calls:
                    result = execute_tool_call(
                        context=tool_context,
                        tool_call_id=call.id,
                        name=call.function.name,
                        raw_arguments=call.function.arguments,
                    )
                    self.telemetry.track_event(
                        "tool_call",
                        {"name": result.name, "succeeded": result.succeeded},
                    )
                    content_json = json.dumps(result.content)[:_MAX_TOOL_RESULT_CHARS]
                    tool_msg = SessionMessage(
                        role="tool",
                        content=content_json,
                        tool_call_id=result.tool_call_id,
                        name=result.name,
                    )
                    messages.append(_session_message_to_wire(tool_msg))
                    new_messages.append(tool_msg)
                    actions.append(
                        VoiceTurnAction(
                            type=result.name,
                            status="completed" if result.succeeded else "failed",
                            summary=content_json[:500],
                        )
                    )
                continue

            reply_text = message.content or ""
            final_msg = SessionMessage(role="assistant", content=reply_text)
            new_messages.append(final_msg)
            return OrchestratorResult(
                reply_text=reply_text,
                actions=actions,
                new_messages=new_messages,
                model=model_name,
                finish_reason=finish_reason,
            )

        raise UpstreamServiceError(
            "Assistant did not produce a final response within the tool-call budget."
        )


def _session_message_to_wire(message: SessionMessage) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role}
    if message.content is not None:
        wire["content"] = message.content
    elif message.role != "tool":
        wire["content"] = None
    if message.tool_calls is not None:
        wire["tool_calls"] = message.tool_calls
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    if message.name is not None:
        wire["name"] = message.name
    return wire
