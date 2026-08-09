from __future__ import annotations

from datetime import datetime, timezone

import pytest

from home_assistant_api.errors import ConflictError, NotFoundError
from home_assistant_api.repositories.devices import InMemoryDevicesRepository
from home_assistant_api.repositories.idempotency import InMemoryIdempotencyRepository
from home_assistant_api.repositories.reminders import InMemoryRemindersRepository
from home_assistant_api.repositories.sessions import InMemorySessionsRepository, SessionMessage
from home_assistant_api.repositories.todos import InMemoryTodosRepository


class TestTodosRepository:
    def test_create_and_list(self):
        repo = InMemoryTodosRepository()
        repo.create("device-1", "Buy milk")
        todos = repo.list_for_device("device-1")
        assert len(todos) == 1
        assert todos[0].title == "Buy milk"
        assert todos[0].done is False

    def test_list_excludes_done_by_default(self):
        repo = InMemoryTodosRepository()
        todo = repo.create("device-1", "Buy milk")
        repo.complete("device-1", todo.todo_id)
        assert repo.list_for_device("device-1") == []
        assert len(repo.list_for_device("device-1", include_done=True)) == 1

    def test_complete_unknown_todo_raises(self):
        repo = InMemoryTodosRepository()
        with pytest.raises(NotFoundError):
            repo.complete("device-1", "missing-id")

    def test_complete_wrong_device_raises(self):
        repo = InMemoryTodosRepository()
        todo = repo.create("device-1", "Buy milk")
        with pytest.raises(NotFoundError):
            repo.complete("device-2", todo.todo_id)

    def test_todos_are_isolated_per_device(self):
        repo = InMemoryTodosRepository()
        repo.create("device-1", "Buy milk")
        repo.create("device-2", "Walk dog")
        assert len(repo.list_for_device("device-1")) == 1
        assert len(repo.list_for_device("device-2")) == 1


class TestRemindersRepository:
    def test_create_and_list(self):
        repo = InMemoryRemindersRepository()
        repo.create("device-1", "Take medicine", "2099-01-01T00:00:00Z")
        reminders = repo.list_for_device("device-1")
        assert len(reminders) == 1
        assert reminders[0].delivered is False

    def test_create_rejects_invalid_due_at(self):
        repo = InMemoryRemindersRepository()
        with pytest.raises(ValueError):
            repo.create("device-1", "Take medicine", "not-a-date")

    def test_list_due_only_returns_past_due_undelivered(self):
        repo = InMemoryRemindersRepository()
        past = repo.create("device-1", "Past reminder", "2000-01-01T00:00:00Z")
        repo.create("device-1", "Future reminder", "2099-01-01T00:00:00Z")
        due = repo.list_due("device-1")
        assert [r.reminder_id for r in due] == [past.reminder_id]

    def test_acknowledge_marks_delivered(self):
        repo = InMemoryRemindersRepository()
        reminder = repo.create("device-1", "Past reminder", "2000-01-01T00:00:00Z")
        updated = repo.acknowledge("device-1", reminder.reminder_id)
        assert updated.delivered is True
        assert updated.delivered_at is not None
        assert repo.list_due("device-1") == []

    def test_cancel_marks_cancelled_and_excludes_from_due(self):
        repo = InMemoryRemindersRepository()
        reminder = repo.create("device-1", "Past reminder", "2000-01-01T00:00:00Z")
        repo.cancel("device-1", reminder.reminder_id)
        assert repo.list_due("device-1") == []

    def test_acknowledge_unknown_reminder_raises(self):
        repo = InMemoryRemindersRepository()
        with pytest.raises(NotFoundError):
            repo.acknowledge("device-1", "missing")

    def test_list_due_respects_as_of(self):
        repo = InMemoryRemindersRepository()
        reminder = repo.create("device-1", "Soon", "2030-06-01T00:00:00Z")
        assert repo.list_due("device-1", as_of=datetime(2020, 1, 1, tzinfo=timezone.utc)) == []
        due = repo.list_due("device-1", as_of=datetime(2031, 1, 1, tzinfo=timezone.utc))
        assert [r.reminder_id for r in due] == [reminder.reminder_id]


class TestSessionsRepository:
    def test_get_or_create_generates_conversation_id(self):
        repo = InMemorySessionsRepository()
        session = repo.get_or_create("device-1", None)
        assert session.conversation_id
        assert session.device_id == "device-1"

    def test_get_or_create_returns_same_session_for_known_conversation(self):
        repo = InMemorySessionsRepository()
        first = repo.get_or_create("device-1", None)
        second = repo.get_or_create("device-1", first.conversation_id)
        assert second is first

    def test_conversation_id_reuse_across_devices_creates_new_session(self):
        repo = InMemorySessionsRepository()
        first = repo.get_or_create("device-1", None)
        second = repo.get_or_create("device-2", first.conversation_id)
        assert second.conversation_id != first.conversation_id
        assert second.device_id == "device-2"

    def test_append_and_get_history(self):
        repo = InMemorySessionsRepository()
        session = repo.get_or_create("device-1", None)
        repo.append_message(
            "device-1",
            session.conversation_id,
            SessionMessage(role="user", content="hi"),
        )
        history = repo.get_history("device-1", session.conversation_id)
        assert len(history) == 1
        assert history[0].content == "hi"

    def test_history_is_trimmed_to_max_messages(self):
        repo = InMemorySessionsRepository(max_messages=3)
        session = repo.get_or_create("device-1", None)
        for i in range(5):
            repo.append_message(
                "device-1",
                session.conversation_id,
                SessionMessage(role="user", content=str(i)),
            )
        history = repo.get_history("device-1", session.conversation_id)
        assert len(history) == 3
        assert [m.content for m in history] == ["2", "3", "4"]

    def test_get_history_unknown_conversation_returns_empty(self):
        repo = InMemorySessionsRepository()
        assert repo.get_history("device-1", "missing") == []

    def test_history_and_append_require_owning_device(self):
        repo = InMemorySessionsRepository()
        session = repo.get_or_create("device-1", None)

        repo.append_message(
            "device-2",
            session.conversation_id,
            SessionMessage(role="user", content="unauthorized"),
        )

        assert repo.get_history("device-2", session.conversation_id) == []
        assert repo.get_history("device-1", session.conversation_id) == []


class TestDevicesRepository:
    def test_register_and_get(self):
        repo = InMemoryDevicesRepository()
        record = repo.register("device-1", "Kitchen Pi", "hash1")
        assert repo.get("device-1") == record

    def test_register_duplicate_raises_conflict(self):
        repo = InMemoryDevicesRepository()
        repo.register("device-1", "Kitchen Pi", "hash1")
        with pytest.raises(ConflictError):
            repo.register("device-1", "Kitchen Pi", "hash2")

    def test_require_unknown_device_raises_not_found(self):
        repo = InMemoryDevicesRepository()
        with pytest.raises(NotFoundError):
            repo.require("missing")

    def test_upsert_token_creates_when_missing(self):
        repo = InMemoryDevicesRepository()
        record = repo.upsert_token("device-1", "Kitchen Pi", "hash1")
        assert record.token_hash == "hash1"

    def test_upsert_token_rotates_existing(self):
        repo = InMemoryDevicesRepository()
        repo.register("device-1", "Kitchen Pi", "hash1")
        updated = repo.upsert_token("device-1", "Kitchen Pi", "hash2")
        assert updated.token_hash == "hash2"

    def test_touch_last_seen_updates_timestamp(self):
        repo = InMemoryDevicesRepository()
        repo.register("device-1", "Kitchen Pi", "hash1")
        assert repo.get("device-1").last_seen_at is None
        repo.touch_last_seen("device-1")
        assert repo.get("device-1").last_seen_at is not None

    def test_touch_last_seen_unknown_device_raises(self):
        repo = InMemoryDevicesRepository()
        with pytest.raises(NotFoundError):
            repo.touch_last_seen("missing")

    def test_list_all_returns_every_device(self):
        repo = InMemoryDevicesRepository()
        repo.register("device-1", "Kitchen Pi", "hash1")
        repo.register("device-2", "Bedroom Pi", "hash2")
        assert {d.device_id for d in repo.list_all()} == {"device-1", "device-2"}


class TestIdempotencyRepository:
    def test_reserve_first_use_returns_none(self):
        repo = InMemoryIdempotencyRepository()
        assert repo.reserve("key-1", "fingerprint-1") is None

    def test_reserve_replay_with_same_fingerprint_returns_cached_record(self):
        repo = InMemoryIdempotencyRepository()
        repo.complete("key-1", "fingerprint-1", {"ok": True}, 200, ttl_seconds=60)
        record = repo.reserve("key-1", "fingerprint-1")
        assert record is not None
        assert record.response_body == {"ok": True}
        assert record.status_code == 200

    def test_reserve_replay_with_different_fingerprint_raises_conflict(self):
        repo = InMemoryIdempotencyRepository()
        repo.complete("key-1", "fingerprint-1", {"ok": True}, 200, ttl_seconds=60)
        with pytest.raises(ConflictError):
            repo.reserve("key-1", "different-fingerprint")

    def test_expired_completed_record_is_treated_as_new(self):
        repo = InMemoryIdempotencyRepository()
        repo.complete("key-1", "fingerprint-1", {"ok": True}, 200, ttl_seconds=-1)
        assert repo.reserve("key-1", "fingerprint-1") is None

    def test_reserve_while_pending_raises_conflict_same_fingerprint(self):
        repo = InMemoryIdempotencyRepository()
        assert repo.reserve("key-1", "fingerprint-1") is None
        # A concurrent duplicate of the *same* in-flight request must not
        # be allowed to proceed and double-process; only a completed
        # reservation can ever be replayed.
        with pytest.raises(ConflictError):
            repo.reserve("key-1", "fingerprint-1")

    def test_reserve_while_pending_raises_conflict_different_fingerprint(self):
        repo = InMemoryIdempotencyRepository()
        assert repo.reserve("key-1", "fingerprint-1") is None
        with pytest.raises(ConflictError):
            repo.reserve("key-1", "fingerprint-2")

    def test_release_allows_retry_with_same_body(self):
        repo = InMemoryIdempotencyRepository()
        assert repo.reserve("key-1", "fingerprint-1") is None
        repo.release("key-1", "fingerprint-1")
        # After release, the key is available again for the same body.
        assert repo.reserve("key-1", "fingerprint-1") is None

    def test_release_is_a_no_op_for_completed_reservation(self):
        repo = InMemoryIdempotencyRepository()
        repo.complete("key-1", "fingerprint-1", {"ok": True}, 200, ttl_seconds=60)
        repo.release("key-1", "fingerprint-1")
        record = repo.reserve("key-1", "fingerprint-1")
        assert record is not None
        assert record.response_body == {"ok": True}

    def test_release_is_a_no_op_for_unknown_key(self):
        repo = InMemoryIdempotencyRepository()
        repo.release("does-not-exist", "fingerprint-1")  # must not raise
        assert repo.reserve("does-not-exist", "fingerprint-1") is None

    def test_complete_then_reserve_process_then_complete_lifecycle(self):
        repo = InMemoryIdempotencyRepository()
        assert repo.reserve("key-1", "fingerprint-1") is None
        repo.complete("key-1", "fingerprint-1", {"processed": True}, 200, ttl_seconds=60)
        cached = repo.reserve("key-1", "fingerprint-1")
        assert cached is not None
        assert cached.response_body == {"processed": True}
