"""Idempotency-key repository.

Backs the ``Idempotency-Key`` header required by ``POST /voice-turn``.
Replaying the same key with the same request body returns the cached
response without reprocessing; replaying it with a different body -- or
while the original request is still being processed -- is a client error
(``ConflictError``), never a silent overwrite or a duplicate side effect.

Lifecycle (``reserve`` -> process -> ``complete``/``release``):

1. ``reserve(key, fingerprint)`` atomically claims ``key`` for
   ``fingerprint``. It returns ``None`` when the caller must process the
   request (a fresh reservation was created), or a cached
   :class:`IdempotentRecord` when a *completed* prior response can be
   replayed verbatim. It raises :class:`ConflictError` when ``key`` is
   already reserved by a different fingerprint, or is currently pending
   (an in-flight duplicate).
2. On success, the caller calls ``complete(...)`` with the response to
   cache for the TTL.
3. On failure, the caller calls ``release(...)`` to drop the reservation so
   a retry of the *same* request can succeed instead of being permanently
   poisoned by a crashed or errored first attempt.

A pending reservation that is never completed or released (e.g. a worker
crash) also expires on its own after ``_PENDING_RESERVATION_TTL_SECONDS``,
so a stuck key cannot block retries forever.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Protocol

from azure.core import MatchConditions
from azure.data.tables import TableClient, UpdateMode

from home_assistant_api.errors import ConflictError
from home_assistant_api.repositories.table_storage import (
    HttpResponseError,
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
    TableBackedRepositoryMixin,
    raise_upstream_error,
)
from home_assistant_api.time_utils import parse_iso8601, to_iso8601, utc_now

# Safety net for a reservation whose worker crashed before calling complete()
# or release(): after this long a "pending" entry is treated as abandoned
# and a fresh reservation is allowed, rather than poisoning the key forever.
_PENDING_RESERVATION_TTL_SECONDS = 120

_STATUS_PENDING = "pending"
_STATUS_COMPLETED = "completed"


@dataclass(frozen=True)
class IdempotentRecord:
    request_fingerprint: str
    response_body: Dict[str, Any]
    status_code: int
    expires_at: datetime


@dataclass
class _Entry:
    status: str
    fingerprint: str
    expires_at: datetime
    response_body: Optional[Dict[str, Any]] = None
    status_code: Optional[int] = None


class IdempotencyRepository(Protocol):
    def reserve(self, key: str, request_fingerprint: str) -> Optional[IdempotentRecord]:
        """Atomically claim ``key`` for ``request_fingerprint``.

        Returns ``None`` if a new pending reservation was created (the
        caller must process the request then call :meth:`complete` or
        :meth:`release`). Returns a cached :class:`IdempotentRecord` if
        ``key`` already completed with a matching fingerprint. Raises
        :class:`ConflictError` if ``key`` is reserved by a different
        fingerprint, or is currently pending (an in-flight duplicate).
        """

    def complete(
        self,
        key: str,
        request_fingerprint: str,
        response_body: Dict[str, Any],
        status_code: int,
        ttl_seconds: int,
    ) -> None:
        """Record the successful response for a previously reserved key."""

    def release(self, key: str, request_fingerprint: str) -> None:
        """Drop a pending reservation so a retry of the same body can succeed.

        A no-op if the reservation is already completed or no longer
        present -- callers invoke this unconditionally from a ``finally``
        block, so it must never raise for an already-resolved key.
        """


class InMemoryIdempotencyRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, _Entry] = {}

    def reserve(self, key: str, request_fingerprint: str) -> Optional[IdempotentRecord]:
        with self._lock:
            now = utc_now()
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at <= now:
                del self._entries[key]
                entry = None

            if entry is None:
                self._entries[key] = _Entry(
                    status=_STATUS_PENDING,
                    fingerprint=request_fingerprint,
                    expires_at=now + timedelta(seconds=_PENDING_RESERVATION_TTL_SECONDS),
                )
                return None

            if entry.status == _STATUS_PENDING:
                # Either a genuine concurrent duplicate, or a different body
                # racing an in-flight request for the same key -- both are
                # conflicts; only a completed reservation can be replayed.
                raise ConflictError(
                    "Idempotency-Key is already being processed by another request."
                )

            # status == completed
            if entry.fingerprint != request_fingerprint:
                raise ConflictError(
                    "Idempotency-Key was already used with a different request body."
                )
            assert entry.response_body is not None and entry.status_code is not None
            return IdempotentRecord(
                request_fingerprint=entry.fingerprint,
                response_body=entry.response_body,
                status_code=entry.status_code,
                expires_at=entry.expires_at,
            )

    def complete(
        self,
        key: str,
        request_fingerprint: str,
        response_body: Dict[str, Any],
        status_code: int,
        ttl_seconds: int,
    ) -> None:
        with self._lock:
            self._entries[key] = _Entry(
                status=_STATUS_COMPLETED,
                fingerprint=request_fingerprint,
                expires_at=utc_now() + timedelta(seconds=ttl_seconds),
                response_body=response_body,
                status_code=status_code,
            )

    def release(self, key: str, request_fingerprint: str) -> None:
        with self._lock:
            entry = self._entries.get(key)
            if (
                entry is not None
                and entry.status == _STATUS_PENDING
                and entry.fingerprint == request_fingerprint
            ):
                del self._entries[key]


class TableIdempotencyRepository(TableBackedRepositoryMixin):
    """Azure Table Storage implementation backed by the ``Idempotency`` table.

    Uses ``create_entity`` (fails atomically if the row already exists) for
    the pending reservation, and an ETag-conditional ``delete_entity``/
    ``upsert_entity`` for ``release``/``complete`` so concurrent callers
    across Function workers cannot both "win" a reservation or clobber each
    other's completion. Both PartitionKey and RowKey are the idempotency key
    itself: a single, small partition per key is the correct shape for a
    point lookup keyed only by the header value.
    """

    def __init__(self, table_client: TableClient) -> None:
        self._table = table_client
        self._table_ensured = False

    def reserve(self, key: str, request_fingerprint: str) -> Optional[IdempotentRecord]:
        self._ensure_table()
        now = utc_now()
        pending_entity = {
            "PartitionKey": key,
            "RowKey": key,
            "Status": _STATUS_PENDING,
            "Fingerprint": request_fingerprint,
            "ExpiresAtUtc": to_iso8601(
                now + timedelta(seconds=_PENDING_RESERVATION_TTL_SECONDS)
            ),
        }
        try:
            self._table.create_entity(pending_entity)
            return None
        except ResourceExistsError:
            pass
        except HttpResponseError as exc:
            raise_upstream_error("reserve_idempotency_key", self._table.table_name, exc)

        try:
            existing = self._table.get_entity(key, key)
        except ResourceNotFoundError:
            # Raced with a release()/expiry between the failed create and
            # this read -- retry the reservation once, atomically.
            return self.reserve(key, request_fingerprint)
        except HttpResponseError as exc:
            raise_upstream_error("get_idempotency_key", self._table.table_name, exc)
            raise  # pragma: no cover - raise_upstream_error always raises

        expires_at = parse_iso8601(str(existing["ExpiresAtUtc"]))
        if expires_at <= now:
            self._delete_if_unmodified(existing)
            return self.reserve(key, request_fingerprint)

        status = str(existing["Status"])
        fingerprint = str(existing["Fingerprint"])
        if status == _STATUS_PENDING:
            raise ConflictError(
                "Idempotency-Key is already being processed by another request."
            )
        if fingerprint != request_fingerprint:
            raise ConflictError(
                "Idempotency-Key was already used with a different request body."
            )
        return IdempotentRecord(
            request_fingerprint=fingerprint,
            response_body=json.loads(str(existing["ResponseJson"])),
            status_code=int(existing["StatusCode"]),
            expires_at=expires_at,
        )

    def _delete_if_unmodified(self, existing: Dict[str, Any]) -> None:
        try:
            self._table.delete_entity(
                existing["PartitionKey"],
                existing["RowKey"],
                etag=existing.metadata.get("etag"),
                match_condition=MatchConditions.IfNotModified,
            )
        except (ResourceNotFoundError, ResourceModifiedError):
            # Someone else already deleted or replaced it -- fine, the
            # caller's retry will observe the new state.
            return
        except HttpResponseError as exc:
            raise_upstream_error("expire_idempotency_key", self._table.table_name, exc)

    def complete(
        self,
        key: str,
        request_fingerprint: str,
        response_body: Dict[str, Any],
        status_code: int,
        ttl_seconds: int,
    ) -> None:
        self._ensure_table()
        entity = {
            "PartitionKey": key,
            "RowKey": key,
            "Status": _STATUS_COMPLETED,
            "Fingerprint": request_fingerprint,
            "ResponseJson": json.dumps(response_body),
            "StatusCode": status_code,
            "ExpiresAtUtc": to_iso8601(utc_now() + timedelta(seconds=ttl_seconds)),
        }
        try:
            self._table.upsert_entity(entity, mode=UpdateMode.REPLACE)
        except HttpResponseError as exc:
            raise_upstream_error("complete_idempotency_key", self._table.table_name, exc)

    def release(self, key: str, request_fingerprint: str) -> None:
        self._ensure_table()
        try:
            existing = self._table.get_entity(key, key)
        except ResourceNotFoundError:
            return
        except HttpResponseError as exc:
            raise_upstream_error("get_idempotency_key", self._table.table_name, exc)
            return
        if str(existing.get("Status")) != _STATUS_PENDING:
            return
        if str(existing.get("Fingerprint")) != request_fingerprint:
            return
        try:
            self._table.delete_entity(
                existing["PartitionKey"],
                existing["RowKey"],
                etag=existing.metadata.get("etag"),
                match_condition=MatchConditions.IfNotModified,
            )
        except (ResourceNotFoundError, ResourceModifiedError):
            return
        except HttpResponseError as exc:
            raise_upstream_error("release_idempotency_key", self._table.table_name, exc)
