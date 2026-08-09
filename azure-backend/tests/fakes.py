"""A minimal, in-process fake of ``azure.data.tables.TableClient``.

Real Azure Table Storage semantics this fake reproduces, precisely because
the production ``Table*Repository`` classes depend on them for correctness:

- ``create_entity`` fails atomically (``ResourceExistsError``) if the
  (PartitionKey, RowKey) pair already exists -- this is what makes
  idempotency-key reservation and device registration race-safe.
- ``get_entity``/``update_entity``/``delete_entity`` raise
  ``ResourceNotFoundError`` for a missing row.
- ``delete_entity``/``update_entity`` honor an ``etag`` +
  ``match_condition=MatchConditions.IfNotModified`` pair, raising
  ``ResourceModifiedError`` on a stale etag -- required for the idempotency
  repository's optimistic-concurrency release/expiry path.
- ``query_entities`` supports the narrow subset of OData filter syntax this
  codebase actually emits: ``PartitionKey eq '...'`` and/or
  ``RowKey eq '...'``, optionally ``and``-combined.

This fake requires no network access and no Azure credentials, so every test
using it runs fully offline.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)
from azure.data.tables import UpdateMode

_FILTER_CLAUSE = re.compile(r"(PartitionKey|RowKey) eq '([^']*)'")


class FakeTableEntity(dict):
    """A plain ``dict`` with the ``.metadata`` attribute real entities expose."""

    def __init__(self, data: Dict[str, Any], etag: str) -> None:
        super().__init__(data)
        self.metadata = {"etag": etag}


class FakeTableClient:
    """Records every entity written so tests can assert exact field shapes."""

    def __init__(self, table_name: str = "FakeTable") -> None:
        self.table_name = table_name
        self.table_created = False
        self.create_table_calls = 0
        self._store: Dict[Tuple[str, str], FakeTableEntity] = {}
        self._etag_counter = 0

    # -- table lifecycle ----------------------------------------------------
    def create_table(self) -> None:
        self.create_table_calls += 1
        if self.table_created:
            raise ResourceExistsError("Table already exists.")
        self.table_created = True

    # -- helpers --------------------------------------------------------------
    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f'W/"etag-{self._etag_counter}"'

    @staticmethod
    def _key_of(entity: Dict[str, Any]) -> Tuple[str, str]:
        return (str(entity["PartitionKey"]), str(entity["RowKey"]))

    def _check_etag(self, key: Tuple[str, str], etag: Optional[str], match_condition: Any) -> None:
        if match_condition is None or etag is None:
            return
        if match_condition != MatchConditions.IfNotModified:
            return
        current = self._store.get(key)
        if current is None:
            raise ResourceNotFoundError("Entity does not exist.")
        if current.metadata["etag"] != etag:
            raise ResourceModifiedError("Etag does not match; entity was modified.")

    # -- CRUD -----------------------------------------------------------------
    def create_entity(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        key = self._key_of(entity)
        if key in self._store:
            raise ResourceExistsError(f"Entity {key} already exists.")
        etag = self._next_etag()
        self._store[key] = FakeTableEntity(dict(entity), etag)
        return {"etag": etag}

    def get_entity(self, partition_key: str, row_key: str) -> FakeTableEntity:
        key = (str(partition_key), str(row_key))
        stored = self._store.get(key)
        if stored is None:
            raise ResourceNotFoundError(f"Entity {key} was not found.")
        return FakeTableEntity(dict(stored), stored.metadata["etag"])

    def upsert_entity(
        self, entity: Dict[str, Any], *, mode: UpdateMode = UpdateMode.MERGE
    ) -> Dict[str, Any]:
        key = self._key_of(entity)
        etag = self._next_etag()
        if mode == UpdateMode.MERGE and key in self._store:
            merged = dict(self._store[key])
            merged.update(entity)
            self._store[key] = FakeTableEntity(merged, etag)
        else:
            self._store[key] = FakeTableEntity(dict(entity), etag)
        return {"etag": etag}

    def update_entity(
        self,
        entity: Dict[str, Any],
        *,
        mode: UpdateMode = UpdateMode.MERGE,
        etag: Optional[str] = None,
        match_condition: Any = None,
    ) -> Dict[str, Any]:
        key = self._key_of(entity)
        if key not in self._store:
            raise ResourceNotFoundError(f"Entity {key} was not found.")
        self._check_etag(key, etag, match_condition)
        new_etag = self._next_etag()
        if mode == UpdateMode.MERGE:
            merged = dict(self._store[key])
            merged.update(entity)
            self._store[key] = FakeTableEntity(merged, new_etag)
        else:
            self._store[key] = FakeTableEntity(dict(entity), new_etag)
        return {"etag": new_etag}

    def delete_entity(
        self,
        partition_key: str,
        row_key: str,
        *,
        etag: Optional[str] = None,
        match_condition: Any = None,
    ) -> None:
        key = (str(partition_key), str(row_key))
        if key not in self._store:
            raise ResourceNotFoundError(f"Entity {key} was not found.")
        self._check_etag(key, etag, match_condition)
        del self._store[key]

    def query_entities(self, query_filter: str) -> Iterable[FakeTableEntity]:
        clauses = _FILTER_CLAUSE.findall(query_filter)
        results: List[FakeTableEntity] = []
        for (pk, rk), entity in self._store.items():
            matched = True
            for field_name, value in clauses:
                actual = pk if field_name == "PartitionKey" else rk
                if actual != value:
                    matched = False
                    break
            if matched:
                results.append(FakeTableEntity(dict(entity), entity.metadata["etag"]))
        return results

    # -- test-only introspection helpers ---------------------------------------
    def raw_entities(self) -> List[Dict[str, Any]]:
        """Return every stored entity as a plain dict, for field-shape assertions."""

        return [dict(entity) for entity in self._store.values()]
