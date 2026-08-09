"""Todo list repository used by the ``todos`` assistant tools."""

from __future__ import annotations

import threading
import uuid
from typing import Any, Dict, List, Mapping, Optional, Protocol

from azure.data.tables import TableClient, UpdateMode

from home_assistant_api.errors import NotFoundError
from home_assistant_api.models import Todo
from home_assistant_api.repositories.table_storage import (
    HttpResponseError,
    ResourceNotFoundError,
    TableBackedRepositoryMixin,
    raise_upstream_error,
)
from home_assistant_api.time_utils import to_iso8601, utc_now


class TodosRepository(Protocol):
    def create(self, device_id: str, title: str, due_at: Optional[str] = None) -> Todo:
        ...

    def list_for_device(self, device_id: str, *, include_done: bool = False) -> List[Todo]:
        ...

    def complete(self, device_id: str, todo_id: str) -> Todo:
        """Mark a todo done. Raises NotFoundError if it does not exist."""


class InMemoryTodosRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._todos: Dict[str, Todo] = {}

    def create(self, device_id: str, title: str, due_at: Optional[str] = None) -> Todo:
        now = to_iso8601(utc_now())
        with self._lock:
            todo = Todo(
                todo_id=str(uuid.uuid4()),
                device_id=device_id,
                title=title,
                done=False,
                due_at=due_at,
                created_at=now,
                updated_at=now,
            )
            self._todos[todo.todo_id] = todo
            return todo

    def list_for_device(self, device_id: str, *, include_done: bool = False) -> List[Todo]:
        with self._lock:
            items = [t for t in self._todos.values() if t.device_id == device_id]
        if not include_done:
            items = [t for t in items if not t.done]
        return sorted(items, key=lambda t: t.created_at)

    def complete(self, device_id: str, todo_id: str) -> Todo:
        with self._lock:
            todo = self._todos.get(todo_id)
            if todo is None or todo.device_id != device_id:
                raise NotFoundError(f"Todo '{todo_id}' was not found for this device.")
            updated = todo.model_copy(update={"done": True, "updated_at": to_iso8601(utc_now())})
            self._todos[todo_id] = updated
            return updated


def _entity_to_todo(entity: Mapping[str, Any]) -> Todo:
    return Todo(
        todo_id=str(entity["RowKey"]),
        device_id=str(entity["PartitionKey"]),
        title=str(entity["Title"]),
        done=bool(entity.get("Done", False)),
        due_at=entity.get("DueAt"),
        created_at=str(entity["CreatedAtUtc"]),
        updated_at=str(entity["UpdatedAtUtc"]),
    )


def _todo_to_entity(todo: Todo) -> Dict[str, Any]:
    entity: Dict[str, Any] = {
        "PartitionKey": todo.device_id,
        "RowKey": todo.todo_id,
        "Title": todo.title,
        "Done": todo.done,
        "CreatedAtUtc": todo.created_at,
        "UpdatedAtUtc": todo.updated_at,
    }
    if todo.due_at is not None:
        entity["DueAt"] = todo.due_at
    return entity


class TableTodosRepository(TableBackedRepositoryMixin):
    """Azure Table Storage implementation backed by the ``Todos`` table.

    PartitionKey is the owning device id, RowKey is the todo id, so listing
    a device's todos is a single-partition query.
    """

    def __init__(self, table_client: TableClient) -> None:
        self._table = table_client
        self._table_ensured = False

    def create(self, device_id: str, title: str, due_at: Optional[str] = None) -> Todo:
        self._ensure_table()
        now = to_iso8601(utc_now())
        todo = Todo(
            todo_id=str(uuid.uuid4()),
            device_id=device_id,
            title=title,
            done=False,
            due_at=due_at,
            created_at=now,
            updated_at=now,
        )
        try:
            self._table.create_entity(_todo_to_entity(todo))
        except HttpResponseError as exc:
            raise_upstream_error("create_todo", self._table.table_name, exc)
        return todo

    def list_for_device(self, device_id: str, *, include_done: bool = False) -> List[Todo]:
        self._ensure_table()
        try:
            entities = self._table.query_entities(f"PartitionKey eq '{device_id}'")
            items = [_entity_to_todo(entity) for entity in entities]
        except HttpResponseError as exc:
            raise_upstream_error("list_todos", self._table.table_name, exc)
            raise  # pragma: no cover - raise_upstream_error always raises
        if not include_done:
            items = [t for t in items if not t.done]
        return sorted(items, key=lambda t: t.created_at)

    def complete(self, device_id: str, todo_id: str) -> Todo:
        self._ensure_table()
        try:
            entity = self._table.get_entity(device_id, todo_id)
        except ResourceNotFoundError as exc:
            raise NotFoundError(f"Todo '{todo_id}' was not found for this device.") from exc
        except HttpResponseError as exc:
            raise_upstream_error("get_todo", self._table.table_name, exc)
        todo = _entity_to_todo(entity).model_copy(
            update={"done": True, "updated_at": to_iso8601(utc_now())}
        )
        try:
            self._table.update_entity(_todo_to_entity(todo), mode=UpdateMode.REPLACE)
        except HttpResponseError as exc:
            raise_upstream_error("complete_todo", self._table.table_name, exc)
        return todo
