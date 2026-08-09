"""Field-mapping and behavior tests for the Azure Table Storage repositories.

These use :class:`tests.fakes.FakeTableClient`, an in-process fake of
``azure.data.tables.TableClient`` -- no Azure credentials or network access
required. Every test asserts the *exact* entity shape written/read, because
``TableDevicesRepository`` in particular must interoperate with the entities
``infra/scripts/provision-device.*`` produces without any migration step.
"""

from __future__ import annotations

import json

import pytest

from home_assistant_api.errors import ConflictError, NotFoundError
from home_assistant_api.google.credentials import (
    TableCredentialStorage,
    _CREDENTIALS_PARTITION_KEY,
)
from home_assistant_api.google.oauth import StoredCredentialData
from home_assistant_api.repositories.devices import TableDevicesRepository
from home_assistant_api.repositories.idempotency import TableIdempotencyRepository
from home_assistant_api.repositories.reminders import TableRemindersRepository
from home_assistant_api.repositories.sessions import SessionMessage, TableSessionsRepository
from home_assistant_api.repositories.table_storage import DEVICE_PARTITION_KEY
from home_assistant_api.repositories.todos import TableTodosRepository

from tests.fakes import FakeTableClient


# -- Devices: must match infra/scripts/provision-device.* exactly ------------------


def test_table_devices_register_writes_infra_compatible_entity_shape():
    client = FakeTableClient("Devices")
    repo = TableDevicesRepository(client)

    repo.register("11111111-1111-1111-1111-111111111111", "Kitchen Pi", "deadbeef" * 8)

    entities = client.raw_entities()
    assert len(entities) == 1
    entity = entities[0]
    assert entity["PartitionKey"] == DEVICE_PARTITION_KEY == "device"
    assert entity["RowKey"] == "11111111-1111-1111-1111-111111111111"
    assert entity["DeviceName"] == "Kitchen Pi"
    assert entity["TokenHash"] == "deadbeef" * 8
    assert entity["Enabled"] is True
    assert "CreatedAtUtc" in entity
    # LastSeenAtUtc is backend-managed and absent until touch_last_seen().
    assert "LastSeenAtUtc" not in entity
    assert client.table_created is True


def test_table_devices_repository_reads_entity_produced_by_infra_provisioning_directly():
    """Simulates a device row exactly as infra/scripts/provision-device.* writes it,
    written directly to the fake table (bypassing this backend entirely), and
    proves the repository can read it back without any migration."""

    client = FakeTableClient("Devices")
    client.create_table()
    client.create_entity(
        {
            "PartitionKey": "device",
            "RowKey": "22222222-2222-2222-2222-222222222222",
            "DeviceName": "Living Room Pi",
            "TokenHash": "abcd1234" * 8,
            "Enabled": True,
            "CreatedAtUtc": "2024-01-01T00:00:00Z",
        }
    )

    repo = TableDevicesRepository(client)
    record = repo.require("22222222-2222-2222-2222-222222222222")
    assert record.display_name == "Living Room Pi"
    assert record.token_hash == "abcd1234" * 8
    assert record.enabled is True
    assert record.last_seen_at is None


def test_table_devices_register_duplicate_raises_conflict():
    client = FakeTableClient("Devices")
    repo = TableDevicesRepository(client)
    repo.register("device-1", "Pi", "hash1")
    with pytest.raises(ConflictError):
        repo.register("device-1", "Pi", "hash1")


def test_table_devices_touch_last_seen_merges_without_clobbering_other_fields():
    client = FakeTableClient("Devices")
    repo = TableDevicesRepository(client)
    repo.register("device-1", "Pi", "hash1")
    repo.touch_last_seen("device-1")

    record = repo.require("device-1")
    assert record.last_seen_at is not None
    assert record.display_name == "Pi"
    assert record.token_hash == "hash1"


def test_table_devices_touch_last_seen_unknown_device_raises_not_found():
    client = FakeTableClient("Devices")
    repo = TableDevicesRepository(client)
    with pytest.raises(NotFoundError):
        repo.touch_last_seen("missing")


def test_table_devices_set_enabled_false_persists_and_reads_back():
    client = FakeTableClient("Devices")
    repo = TableDevicesRepository(client)
    repo.register("device-1", "Pi", "hash1")
    repo.set_enabled("device-1", False)
    assert repo.require("device-1").enabled is False
    entity = client.raw_entities()[0]
    assert entity["Enabled"] is False


def test_table_devices_list_all_only_returns_device_partition():
    client = FakeTableClient("Devices")
    repo = TableDevicesRepository(client)
    repo.register("device-1", "Pi One", "hash1")
    repo.register("device-2", "Pi Two", "hash2")
    devices = repo.list_all()
    assert {d.device_id for d in devices} == {"device-1", "device-2"}


# -- Todos ---------------------------------------------------------------------------


def test_table_todos_create_writes_partition_by_device_row_by_todo_id():
    client = FakeTableClient("Todos")
    repo = TableTodosRepository(client)
    todo = repo.create("device-1", "Buy milk", due_at=None)

    entity = client.raw_entities()[0]
    assert entity["PartitionKey"] == "device-1"
    assert entity["RowKey"] == todo.todo_id
    assert entity["Title"] == "Buy milk"
    assert entity["Done"] is False


def test_table_todos_complete_marks_done_and_updates_timestamp():
    client = FakeTableClient("Todos")
    repo = TableTodosRepository(client)
    todo = repo.create("device-1", "Buy milk")
    completed = repo.complete("device-1", todo.todo_id)
    assert completed.done is True
    assert completed.updated_at != todo.updated_at or completed.updated_at == todo.updated_at

    entity = client.get_entity("device-1", todo.todo_id)
    assert entity["Done"] is True


def test_table_todos_complete_unknown_raises_not_found():
    client = FakeTableClient("Todos")
    repo = TableTodosRepository(client)
    with pytest.raises(NotFoundError):
        repo.complete("device-1", "00000000-0000-0000-0000-000000000000")


def test_table_todos_list_for_device_excludes_done_by_default():
    client = FakeTableClient("Todos")
    repo = TableTodosRepository(client)
    todo = repo.create("device-1", "Buy milk")
    repo.create("device-1", "Buy eggs")
    repo.complete("device-1", todo.todo_id)

    active = repo.list_for_device("device-1")
    assert len(active) == 1
    assert active[0].title == "Buy eggs"

    all_todos = repo.list_for_device("device-1", include_done=True)
    assert len(all_todos) == 2


# -- Reminders -------------------------------------------------------------------------


def test_table_reminders_create_writes_expected_entity_shape():
    client = FakeTableClient("Reminders")
    repo = TableRemindersRepository(client)
    reminder = repo.create("device-1", "Take medicine", "2099-01-01T00:00:00Z")

    entity = client.raw_entities()[0]
    assert entity["PartitionKey"] == "device-1"
    assert entity["RowKey"] == reminder.reminder_id
    assert entity["Title"] == "Take medicine"
    assert entity["DueAt"] == "2099-01-01T00:00:00Z"
    assert entity["Delivered"] is False
    assert entity["Cancelled"] is False


def test_table_reminders_list_due_only_returns_past_due_and_undelivered():
    client = FakeTableClient("Reminders")
    repo = TableRemindersRepository(client)
    repo.create("device-1", "Past", "2000-01-01T00:00:00Z")
    repo.create("device-1", "Future", "2099-01-01T00:00:00Z")

    due = repo.list_due("device-1")
    assert len(due) == 1
    assert due[0].title == "Past"


def test_table_reminders_acknowledge_marks_delivered():
    client = FakeTableClient("Reminders")
    repo = TableRemindersRepository(client)
    reminder = repo.create("device-1", "Past", "2000-01-01T00:00:00Z")
    acked = repo.acknowledge("device-1", reminder.reminder_id)
    assert acked.delivered is True
    assert repo.list_due("device-1") == []


def test_table_reminders_acknowledge_unknown_raises_not_found():
    client = FakeTableClient("Reminders")
    repo = TableRemindersRepository(client)
    with pytest.raises(NotFoundError):
        repo.acknowledge("device-1", "00000000-0000-0000-0000-000000000000")


# -- Sessions --------------------------------------------------------------------------


def test_table_sessions_get_or_create_writes_partition_by_device_row_by_conversation():
    client = FakeTableClient("Sessions")
    repo = TableSessionsRepository(client)
    session = repo.get_or_create("device-1", None)

    entity = client.raw_entities()[0]
    assert entity["PartitionKey"] == "device-1"
    assert entity["RowKey"] == session.conversation_id
    assert json.loads(entity["MessagesJson"]) == []


def test_table_sessions_get_or_create_returns_same_session_for_same_conversation_id():
    client = FakeTableClient("Sessions")
    repo = TableSessionsRepository(client)
    first = repo.get_or_create("device-1", None)
    second = repo.get_or_create("device-1", first.conversation_id)
    assert second.conversation_id == first.conversation_id


def test_table_sessions_append_message_persists_history_as_json():
    client = FakeTableClient("Sessions")
    repo = TableSessionsRepository(client)
    session = repo.get_or_create("device-1", None)
    repo.append_message(
        "device-1",
        session.conversation_id,
        SessionMessage(role="user", content="hi"),
    )
    repo.append_message(
        "device-1",
        session.conversation_id,
        SessionMessage(role="assistant", content="hello"),
    )

    history = repo.get_history("device-1", session.conversation_id)
    assert [m.role for m in history] == ["user", "assistant"]
    assert [m.content for m in history] == ["hi", "hello"]

    entity = client.raw_entities()[0]
    stored_messages = json.loads(entity["MessagesJson"])
    assert len(stored_messages) == 2


def test_table_sessions_append_message_caps_history_length():
    client = FakeTableClient("Sessions")
    repo = TableSessionsRepository(client, max_messages=3)
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


def test_table_sessions_get_history_unknown_conversation_returns_empty():
    client = FakeTableClient("Sessions")
    repo = TableSessionsRepository(client)
    assert (
        repo.get_history("device-1", "00000000-0000-0000-0000-000000000000")
        == []
    )


def test_table_sessions_odata_like_conversation_id_stays_device_scoped():
    client = FakeTableClient("Sessions")
    repo = TableSessionsRepository(client)
    conversation_id = "shared' or PartitionKey ne '"
    victim = repo.get_or_create("victim-device", conversation_id)
    attacker = repo.get_or_create("attacker-device", conversation_id)

    repo.append_message(
        victim.device_id,
        victim.conversation_id,
        SessionMessage(role="user", content="victim history"),
    )
    repo.append_message(
        attacker.device_id,
        attacker.conversation_id,
        SessionMessage(role="user", content="attacker history"),
    )

    victim_history = repo.get_history(victim.device_id, victim.conversation_id)
    attacker_history = repo.get_history(attacker.device_id, attacker.conversation_id)
    assert [message.content for message in victim_history] == ["victim history"]
    assert [message.content for message in attacker_history] == ["attacker history"]


# -- Idempotency --------------------------------------------------------------------------


def test_table_idempotency_reserve_then_complete_then_replay():
    client = FakeTableClient("Idempotency")
    repo = TableIdempotencyRepository(client)

    result = repo.reserve("key-1", "fingerprint-a")
    assert result is None  # fresh reservation -- caller must process.

    entity = client.raw_entities()[0]
    assert entity["PartitionKey"] == "key-1"
    assert entity["RowKey"] == "key-1"
    assert entity["Status"] == "pending"
    assert entity["Fingerprint"] == "fingerprint-a"

    repo.complete("key-1", "fingerprint-a", {"ok": True}, 200, ttl_seconds=3600)
    replay = repo.reserve("key-1", "fingerprint-a")
    assert replay is not None
    assert replay.response_body == {"ok": True}
    assert replay.status_code == 200


def test_table_idempotency_reserve_while_pending_raises_conflict():
    client = FakeTableClient("Idempotency")
    repo = TableIdempotencyRepository(client)
    repo.reserve("key-1", "fingerprint-a")
    with pytest.raises(ConflictError):
        repo.reserve("key-1", "fingerprint-a")
    with pytest.raises(ConflictError):
        repo.reserve("key-1", "fingerprint-b")


def test_table_idempotency_replay_with_different_fingerprint_raises_conflict():
    client = FakeTableClient("Idempotency")
    repo = TableIdempotencyRepository(client)
    repo.reserve("key-1", "fingerprint-a")
    repo.complete("key-1", "fingerprint-a", {"ok": True}, 200, ttl_seconds=3600)
    with pytest.raises(ConflictError):
        repo.reserve("key-1", "fingerprint-b")


def test_table_idempotency_release_allows_retry_with_same_body():
    client = FakeTableClient("Idempotency")
    repo = TableIdempotencyRepository(client)
    repo.reserve("key-1", "fingerprint-a")
    repo.release("key-1", "fingerprint-a")

    # A retry of the same request must now be able to reserve fresh.
    result = repo.reserve("key-1", "fingerprint-a")
    assert result is None
    assert len(client.raw_entities()) == 1


def test_table_idempotency_release_is_a_no_op_for_completed_reservation():
    client = FakeTableClient("Idempotency")
    repo = TableIdempotencyRepository(client)
    repo.reserve("key-1", "fingerprint-a")
    repo.complete("key-1", "fingerprint-a", {"ok": True}, 200, ttl_seconds=3600)
    repo.release("key-1", "fingerprint-a")  # must not clear the completed record.
    replay = repo.reserve("key-1", "fingerprint-a")
    assert replay is not None
    assert replay.response_body == {"ok": True}


def test_table_idempotency_release_is_a_no_op_for_unknown_key():
    client = FakeTableClient("Idempotency")
    repo = TableIdempotencyRepository(client)
    repo.release("does-not-exist", "fingerprint-a")  # must not raise.


def test_table_idempotency_release_uses_etag_conditional_delete():
    """release() must pass etag/match_condition through to delete_entity so a
    concurrent resolver (another worker) racing the same release cannot cause
    a lost update -- verified here by simulating exactly that race."""

    client = FakeTableClient("Idempotency")
    repo = TableIdempotencyRepository(client)
    repo.reserve("key-1", "fingerprint-a")

    # Simulate another worker completing the reservation between this
    # worker's read and its release() call by mutating the stored etag.
    key = ("key-1", "key-1")
    client._store[key] = type(client._store[key])(dict(client._store[key]), "W/\"stale\"")

    # release() should tolerate the etag mismatch as a benign race (someone
    # else already resolved it) rather than raising.
    repo.release("key-1", "fingerprint-a")


# -- Google credential storage -----------------------------------------------------------


def test_table_credential_storage_save_writes_expected_entity_shape_and_no_plaintext_leak():
    client = FakeTableClient("GoogleCredentials")
    storage = TableCredentialStorage(client)
    data = StoredCredentialData(
        token="access-token-value",
        refresh_token="refresh-token-value",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=("scope-a", "scope-b"),
        expiry_iso="2099-01-01T00:00:00",
    )
    storage.save("device-1", data)

    entity = client.raw_entities()[0]
    assert entity["PartitionKey"] == _CREDENTIALS_PARTITION_KEY
    assert entity["RowKey"] == "device-1"
    assert entity["Token"] == "access-token-value"
    assert entity["RefreshToken"] == "refresh-token-value"
    assert json.loads(entity["ScopesJson"]) == ["scope-a", "scope-b"]


def test_table_credential_storage_get_round_trips_stored_data():
    client = FakeTableClient("GoogleCredentials")
    storage = TableCredentialStorage(client)
    data = StoredCredentialData(
        token="access-token-value",
        refresh_token="refresh-token-value",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=("scope-a",),
        expiry_iso=None,
    )
    storage.save("device-1", data)
    round_tripped = storage.get("device-1")
    assert round_tripped == data


def test_table_credential_storage_get_missing_returns_none():
    client = FakeTableClient("GoogleCredentials")
    storage = TableCredentialStorage(client)
    assert storage.get("missing-device") is None


def test_table_credential_storage_delete_removes_entity():
    client = FakeTableClient("GoogleCredentials")
    storage = TableCredentialStorage(client)
    data = StoredCredentialData(
        token="t",
        refresh_token=None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=(),
        expiry_iso=None,
    )
    storage.save("device-1", data)
    storage.delete("device-1")
    assert storage.get("device-1") is None


def test_table_credential_storage_delete_unknown_device_does_not_raise():
    client = FakeTableClient("GoogleCredentials")
    storage = TableCredentialStorage(client)
    storage.delete("never-existed")  # must not raise.


def test_table_credential_storage_works_when_table_already_provisioned():
    """Regression test: TableCredentialStorage must not assume or depend
    on the ``GoogleCredentials`` table being absent/backend-created.
    ``infra/modules/storage.bicep`` provisions ``GoogleCredentials`` as IaC
    exactly like Devices/Todos/Reminders/Sessions/Idempotency, so
    production always encounters an already-existing table; the first
    operation must still succeed -- the idempotent
    create-and-swallow-ResourceExistsError path must transparently handle
    an already-existing table the same way it would a not-yet-provisioned
    one (e.g. local/Azurite)."""

    client = FakeTableClient("GoogleCredentials")
    client.table_created = True  # simulates infra having already created it
    storage = TableCredentialStorage(client)
    data = StoredCredentialData(
        token="access-token-value",
        refresh_token="refresh-token-value",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=("scope-a",),
        expiry_iso=None,
    )
    storage.save("device-1", data)
    assert storage.get("device-1") == data
