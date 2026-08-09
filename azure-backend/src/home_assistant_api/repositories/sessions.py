"""Conversation session repository.

Stores the running message history for a ``conversationId`` so multi-turn
context (including tool call/result pairs) is available to the orchestrator
on the next turn. The in-memory implementation is process-local; the Azure
Table Storage implementation below serializes the (bounded) message history
as a single JSON property per session entity, which comfortably fits Table
Storage's 64KiB per-property limit given the message cap enforced here.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol

from azure.data.tables import TableClient, UpdateMode

from home_assistant_api.repositories.table_storage import (
    HttpResponseError,
    ResourceNotFoundError,
    TableBackedRepositoryMixin,
    raise_upstream_error,
)
from home_assistant_api.time_utils import to_iso8601, utc_now


@dataclass
class SessionMessage:
    role: str
    content: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class Session:
    conversation_id: str
    device_id: str
    created_at: str
    messages: List[SessionMessage] = field(default_factory=list)


class SessionsRepository(Protocol):
    def get_or_create(self, device_id: str, conversation_id: Optional[str]) -> Session:
        ...

    def append_message(
        self, device_id: str, conversation_id: str, message: SessionMessage
    ) -> None:
        ...

    def get_history(self, device_id: str, conversation_id: str) -> List[SessionMessage]:
        ...


class InMemorySessionsRepository:
    def __init__(self, *, max_messages: int = 40) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, Session] = {}
        self._max_messages = max_messages

    def get_or_create(self, device_id: str, conversation_id: Optional[str]) -> Session:
        with self._lock:
            if conversation_id and conversation_id in self._sessions:
                session = self._sessions[conversation_id]
                if session.device_id != device_id:
                    # A conversation id must not be reusable across devices.
                    conversation_id = None
                else:
                    return session
            new_id = conversation_id or str(uuid.uuid4())
            session = Session(
                conversation_id=new_id,
                device_id=device_id,
                created_at=to_iso8601(utc_now()),
            )
            self._sessions[new_id] = session
            return session

    def append_message(
        self, device_id: str, conversation_id: str, message: SessionMessage
    ) -> None:
        with self._lock:
            session = self._sessions.get(conversation_id)
            if session is None or session.device_id != device_id:
                return
            session.messages.append(message)
            if len(session.messages) > self._max_messages:
                del session.messages[: len(session.messages) - self._max_messages]

    def get_history(self, device_id: str, conversation_id: str) -> List[SessionMessage]:
        with self._lock:
            session = self._sessions.get(conversation_id)
            if session is None or session.device_id != device_id:
                return []
            return list(session.messages)


def _messages_to_json(messages: List[SessionMessage]) -> str:
    return json.dumps([asdict(m) for m in messages])


def _messages_from_json(raw: str) -> List[SessionMessage]:
    if not raw:
        return []
    return [SessionMessage(**item) for item in json.loads(raw)]


def _entity_to_session(entity: Mapping[str, Any]) -> Session:
    return Session(
        conversation_id=str(entity["RowKey"]),
        device_id=str(entity["PartitionKey"]),
        created_at=str(entity["CreatedAtUtc"]),
        messages=_messages_from_json(str(entity.get("MessagesJson") or "[]")),
    )


def _session_to_entity(session: Session) -> Dict[str, Any]:
    return {
        "PartitionKey": session.device_id,
        "RowKey": session.conversation_id,
        "CreatedAtUtc": session.created_at,
        "MessagesJson": _messages_to_json(session.messages),
    }


class TableSessionsRepository(TableBackedRepositoryMixin):
    """Azure Table Storage implementation backed by the ``Sessions`` table.

    PartitionKey is the owning device id, RowKey is the conversation id. The
    message history is capped at ``max_messages`` (matching
    :class:`InMemorySessionsRepository`) and serialized as one JSON property
    per entity, so a session round-trips through a single point read/write.
    """

    def __init__(self, table_client: TableClient, *, max_messages: int = 40) -> None:
        self._table = table_client
        self._table_ensured = False
        self._max_messages = max_messages

    def get_or_create(self, device_id: str, conversation_id: Optional[str]) -> Session:
        self._ensure_table()
        if conversation_id:
            existing = self._get(device_id, conversation_id)
            if existing is not None:
                return existing
        new_id = conversation_id or str(uuid.uuid4())
        session = Session(
            conversation_id=new_id,
            device_id=device_id,
            created_at=to_iso8601(utc_now()),
        )
        try:
            self._table.upsert_entity(_session_to_entity(session), mode=UpdateMode.REPLACE)
        except HttpResponseError as exc:
            raise_upstream_error("create_session", self._table.table_name, exc)
        return session

    def _get(self, device_id: str, conversation_id: str) -> Optional[Session]:
        try:
            entity = self._table.get_entity(device_id, conversation_id)
        except ResourceNotFoundError:
            return None
        except HttpResponseError as exc:
            raise_upstream_error("get_session", self._table.table_name, exc)
        return _entity_to_session(entity)

    def append_message(
        self, device_id: str, conversation_id: str, message: SessionMessage
    ) -> None:
        self._ensure_table()
        session = self._get(device_id, conversation_id)
        if session is None:
            return
        session.messages.append(message)
        if len(session.messages) > self._max_messages:
            del session.messages[: len(session.messages) - self._max_messages]
        try:
            self._table.upsert_entity(_session_to_entity(session), mode=UpdateMode.REPLACE)
        except HttpResponseError as exc:
            raise_upstream_error("append_message", self._table.table_name, exc)

    def get_history(self, device_id: str, conversation_id: str) -> List[SessionMessage]:
        self._ensure_table()
        session = self._get(device_id, conversation_id)
        return list(session.messages) if session else []
