"""
Pluggable persistence for the Coordinator.

A Store keeps workspace state between process restarts. The Coordinator
calls ``store.load()`` once at start, and writes after every call that adds
to a workspace's audit log.

How the Coordinator writes depends on what the store implements:

  - ``apply(change)``: the Coordinator sends what the call changed: the new
    audit entries, the objects it touched and the workspace's own fields.
    The cost of a write stays flat as the log grows; it grows with the
    objects a call changes, such as a task with a long history. MemoryStore
    and SqliteStore implement it.
  - ``save(record)`` alone: the Coordinator sends the whole workspace on
    every write, as releases before 0.3.2 did, so the cost of a write grows
    with the log. Stores written for earlier releases keep working this way.

MemoryStore keeps nothing across processes. SqliteStore keeps state in a
SQLite file through the stdlib ``sqlite3`` module. Third-party stores
(Postgres, Redis and so on) implement this interface.

Concurrency model
-----------------

A Coordinator is a **single-writer** component: dispatch is serial, workspace
state is held in memory, and each chain link is computed from the head the
instance holds. Run one Coordinator per workspace.

Every change names the number of audit entries the Coordinator expects the
store to hold already (``base``). MemoryStore and SqliteStore refuse a change
whose base differs from what they hold: they raise StoreConflictError and
write nothing. A second Coordinator writing the same workspace is refused and
reported, and the entries already stored stay as they are. The refused
instance's memory no longer matches the store; restart it to reload.

``save`` makes no such check: it replaces what the store holds for the
workspace. A store that implements only ``save`` therefore still loses
entries silently when two instances write one workspace.

To run more than one instance, partition workspaces so that no workspace is
ever written by two, or hold an external lock. See SPECIFICATION.md 10.3.

Production deployments that need rich queries over the log can project it
into their own analytics store with the ``on_audit`` listener.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable


#: The workspace collections a store keeps object by object.
COLLECTIONS = (
    "members", "tasks", "overrides", "whispers",
    "deliberations", "handoffs", "snapshots", "route_decisions",
)

#: task.create idempotency keys, kept as one more collection of key to task id.
IDEMPOTENCY_KEYS = "idempotency_keys"


@dataclass
class WorkspaceRecord:
    """One persisted workspace snapshot."""

    id:         str
    data:       Any         # JSON-serialisable payload in the snapshot shape
    # For ``save``, a counter of writes. For a record a store loads after
    # ``apply``, the number of audit entries. Recorded for diagnostics;
    # ``save`` does not check it.
    version:    int
    updated_at: str         # ISO-8601 wall-clock timestamp


@dataclass
class StoreChange:
    """One write to one workspace: what one or more calls changed."""

    id: str
    #: Audit entries the store must already hold for this workspace. A store
    #: that holds a different number refuses the change with
    #: StoreConflictError and writes nothing. Not checked when ``replace``.
    base: int
    #: Audit entries the store holds after the change: base + len(entries).
    version: int
    #: Wall-clock timestamp of the write, ISO-8601.
    updated_at: str
    #: The workspace's own fields: everything except its collections and log.
    meta: dict = field(default_factory=dict)
    #: New audit entries in order, in their wire shape. The first has seq base.
    entries: list = field(default_factory=list)
    #: Objects written, by collection and then by key; None removes the object.
    objects: dict = field(default_factory=dict)
    #: Replace everything the store holds for this workspace with this change,
    #: which then carries the whole workspace from seq 0.
    replace: bool = False


class StoreConflictError(Exception):
    """A change whose base does not match the audit entries the store holds.

    Usually a second Coordinator writing the same workspace.
    """

    def __init__(self, workspace: str, held: int, base: int) -> None:
        super().__init__(
            f"The store holds {held} audit entries for workspace {workspace} "
            f"and this write expected {base}, so nothing was written. Another "
            f"coordinator may be writing to this workspace, the store may have "
            f"been changed outside this coordinator, or this coordinator could "
            f"not load the workspace when it started. Restart this coordinator "
            f"to reload.")
        self.workspace = workspace
        self.held = held
        self.base = base


@runtime_checkable
class Store(Protocol):
    """
    Pluggable persistence interface.

    A store may also implement ``apply(change: StoreChange) -> None``; when
    it does, the Coordinator calls it in place of ``save``. ``apply`` must be
    atomic: the whole change is written, or nothing is. Serialise or copy
    what it keeps before returning, since the change refers to live state.
    Refuse a change whose ``base`` differs from the entries held, unless
    ``replace`` is set, by raising StoreConflictError.
    """

    def load(self) -> list[WorkspaceRecord]:
        """Load all workspace records. Called once on coordinator start."""
        ...

    def save(self, record: WorkspaceRecord) -> None:
        """Replace what the store holds for one workspace."""
        ...

    def delete(self, id: str) -> None:
        """Remove one workspace."""
        ...

    def close(self) -> None:
        """Release resources. Idempotent."""
        ...


def object_key(collection: str, item: Any) -> str | None:
    """The key an object is stored under in its collection."""
    if not isinstance(item, dict):
        return None
    if collection == "members":
        key = item.get("uri", item.get("id"))
    else:
        key = item.get("id", item.get("uri"))
    return key if isinstance(key, str) else None


def _keyed(collection: str, value: Any) -> dict:
    """A collection as a snapshot holds it, keyed.

    The Python snapshot holds objects keyed by id and the TypeScript snapshot
    holds arrays; both are read.
    """
    if isinstance(value, dict):
        return dict(value)
    out: dict = {}
    if isinstance(value, list):
        for item in value:
            key = object_key(collection, item)
            if key is not None:
                out[key] = item
    return out


def change_from_record(record: WorkspaceRecord) -> StoreChange:
    """A whole-workspace snapshot as a change that replaces what is held."""
    data = record.data if isinstance(record.data, dict) else {}
    meta: dict = {}
    objects: dict = {}
    entries: list = []
    for name, value in data.items():
        if name == "audit":
            entries = list(value) if isinstance(value, list) else []
        elif name == IDEMPOTENCY_KEYS or name in COLLECTIONS:
            objects[name] = _keyed(name, value)
        else:
            meta[name] = value
    meta.setdefault("id", record.id)
    return StoreChange(id=record.id, base=0, version=record.version,
                       updated_at=record.updated_at, meta=meta,
                       entries=entries, objects=objects, replace=True)


def assemble_snapshot(meta: dict, entries: list,
                      objects: Iterable[tuple[str, str, Any]]) -> dict:
    """Rows read back from a store, in the shape the Coordinator restores.

    Each collection is a dict keyed by id, in the order the objects were
    first written; the log is in seq order.
    """
    data = dict(meta)
    for name in COLLECTIONS:
        data[name] = {}
    data[IDEMPOTENCY_KEYS] = {}
    for collection, key, value in objects:
        if collection == IDEMPOTENCY_KEYS or collection in COLLECTIONS:
            data[collection][key] = value
    data["audit"] = list(entries)
    return data


class MemoryStore:
    """
    In-memory store: it keeps nothing across processes. Like a database it
    keeps each row as JSON text, so a Coordinator restarted on it sees what
    was written and nothing changed later.
    """

    def __init__(self) -> None:
        self._held: dict[str, dict] = {}

    def load(self) -> list[WorkspaceRecord]:
        out: list[WorkspaceRecord] = []
        for id, h in self._held.items():
            objects = [(collection, key, json.loads(value))
                       for collection, items in h["objects"].items()
                       for key, value in items.items()]
            out.append(WorkspaceRecord(
                id=id,
                data=assemble_snapshot(json.loads(h["meta"]),
                                       [json.loads(e) for e in h["entries"]],
                                       objects),
                version=h["version"], updated_at=h["updated_at"]))
        return out

    def save(self, record: WorkspaceRecord) -> None:
        self.apply(change_from_record(record))

    def apply(self, change: StoreChange) -> None:
        # Serialised before anything is held, so a change that cannot be
        # written leaves the store as it was.
        meta = json.dumps(change.meta, sort_keys=True)
        entries = [json.dumps(e, sort_keys=True) for e in change.entries]
        objects = {collection: {key: (None if value is None
                                      else json.dumps(value, sort_keys=True))
                                for key, value in items.items()}
                   for collection, items in change.objects.items()}
        h = self._held.get(change.id)
        if not change.replace:
            holds = len(h["entries"]) if h else 0
            if holds != change.base:
                raise StoreConflictError(change.id, holds, change.base)
        if h is None or change.replace:
            h = {"meta": "{}", "version": 0, "updated_at": "", "entries": [],
                 "objects": {}}
        h["entries"].extend(entries)
        for collection, items in objects.items():
            held = h["objects"].setdefault(collection, {})
            for key, value in items.items():
                if value is None:
                    held.pop(key, None)
                else:
                    held[key] = value
        h["meta"] = meta
        h["version"] = change.version
        h["updated_at"] = change.updated_at
        self._held[change.id] = h

    def delete(self, id: str) -> None:
        self._held.pop(id, None)

    def close(self) -> None:
        self._held.clear()


__all__ = [
    "Store", "WorkspaceRecord", "StoreChange", "StoreConflictError",
    "MemoryStore", "COLLECTIONS", "IDEMPOTENCY_KEYS",
    "change_from_record", "assemble_snapshot", "object_key",
]
