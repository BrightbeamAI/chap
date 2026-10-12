"""
SQLite-backed store using the stdlib `sqlite3` module.

Schema, shared with the TypeScript SqliteStore. Both write these tables, and
the same audit entries for the same calls; the objects are each coordinator's
own, so each restores a file it wrote, and chap-analytics reads a file from
either:

    CREATE TABLE chap_meta (
      workspace  TEXT PRIMARY KEY,
      data       TEXT NOT NULL,    -- the workspace's own fields, JSON
      version    INTEGER NOT NULL, -- audit entries held
      updated_at TEXT NOT NULL     -- ISO-8601
    );
    CREATE TABLE chap_entries (
      workspace TEXT NOT NULL,
      seq       INTEGER NOT NULL,
      data      TEXT NOT NULL,     -- one audit entry, JSON
      PRIMARY KEY (workspace, seq)
    );
    CREATE TABLE chap_objects (
      workspace  TEXT NOT NULL,
      collection TEXT NOT NULL,    -- members, tasks, ..., idempotency_keys
      id         TEXT NOT NULL,
      data       TEXT NOT NULL,    -- one object, JSON
      PRIMARY KEY (workspace, collection, id)
    );

A write adds the call's entries, updates the objects the call changed and
the workspace's own fields, in one transaction, so its cost does not grow
with the log. A write whose first entry is not the next seq the file holds is
refused with StoreConflictError; see the concurrency model in ``store.py``.
Audit entries are only ever inserted, except when a workspace is written
whole, as for a workspace an application put in place itself.

Files written by releases before 0.3.2 hold one JSON snapshot per workspace
in a table named chap_workspaces. Opening such a file moves each workspace
into the tables above, in one transaction, and renames the old table to
chap_workspaces_v1, which is kept as it was.

A view named chap_workspaces takes the old table's place, with the same
columns, so a script that read the old table reads the current state; it
assembles each workspace when read, with collections keyed by id. Releases
before 0.3.2 refuse to open the file, since they cannot write to it.

Usage:
    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_coordinator.storage.sqlite import SqliteStore

    coord = Coordinator(CoordinatorOptions(
        store=SqliteStore("./chap.db"),
    ))

Pass `":memory:"` for an in-memory SQLite instance (faster than
MemoryStore for tests that need transactional semantics).

No external dependencies: the stdlib `sqlite3` module is part of the
Python distribution on all supported platforms.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from .store import (
    COLLECTIONS, IDEMPOTENCY_KEYS, StoreChange, StoreConflictError,
    WorkspaceRecord, assemble_snapshot, change_from_record,
)

_log = logging.getLogger("chap_coordinator.storage")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chap_meta (
    workspace  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chap_entries (
    workspace TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    data      TEXT NOT NULL,
    PRIMARY KEY (workspace, seq)
);
CREATE TABLE IF NOT EXISTS chap_objects (
    workspace  TEXT NOT NULL,
    collection TEXT NOT NULL,
    id         TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (workspace, collection, id)
);
"""

#: The table releases before 0.3.2 wrote, one snapshot per workspace.
_LEGACY_TABLE = "chap_workspaces"

#: A read-only view in the old table's place, for scripts that read it.
_LEGACY_VIEW = (
    f"CREATE VIEW IF NOT EXISTS {_LEGACY_TABLE} AS SELECT m.workspace AS id, json_set(m.data, "
    + ", ".join([
        "'$.audit', json((SELECT json_group_array(json(e.data)) FROM "
        "(SELECT data FROM chap_entries WHERE workspace = m.workspace ORDER BY seq) AS e))",
        *(f"'$.{c}', json((SELECT json_group_object(id, json(data)) FROM chap_objects "
          f"WHERE workspace = m.workspace AND collection = '{c}'))"
          for c in (*COLLECTIONS, IDEMPOTENCY_KEYS)),
    ])
    + ") AS data, m.version AS version, m.updated_at AS updated_at FROM chap_meta AS m"
)


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


class SqliteStore:
    """
    SQLite-backed implementation of the `Store` Protocol, with ``apply``.

    Writes the tables the TypeScript `SqliteStore` writes, and the same audit
    entries for the same calls. The objects are each coordinator's own: each
    restores a file it wrote, and chap-analytics reads a file from either.
    """

    def __init__(self, path: str, *, wal: bool = True) -> None:
        # `check_same_thread=False` allows the Coordinator's persist hook to
        # write from any thread. SQLite serialises writes internally, so this
        # is safe for the single-writer pattern the Coordinator uses.
        # isolation_level=None: transactions are opened and closed here,
        # explicitly, so a write and its conflict check share one.
        self._conn = sqlite3.connect(path, check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row

        if path != ":memory:" and wal:
            try:
                self._conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                # Some filesystems (notably some network mounts) don't
                # support WAL. Fall through silently to default journal
                # mode; correctness is preserved.
                pass

        self._conn.executescript(_SCHEMA)
        self._open()

    # -- transactions ---------------------------------------------------

    def _transaction(self, fn, *args: Any) -> Any:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(*args)
            self._conn.execute("COMMIT")
        except BaseException:
            # A COMMIT that fails, on a busy file say, leaves the transaction
            # open; close it, or every later write fails and the lock is held.
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise
        return result

    def _has_table(self, name: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,)).fetchone() is not None

    def _has_name(self, name: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?",
            (name,)).fetchone() is not None

    # -- the upgrade from releases before 0.3.2 ---------------------------

    def _open(self) -> None:
        """In one transaction, so processes opening the file at once agree:
        move the snapshots a release before 0.3.2 wrote into the tables, then
        put the view in the old table's place."""
        state = {"kept": "", "unreadable": []}

        def move() -> None:
            if self._has_table(_LEGACY_TABLE):
                rows = self._conn.execute(
                    f"SELECT id, data, version, updated_at FROM {_LEGACY_TABLE}"
                ).fetchall()
                for r in rows:
                    # A workspace already in the new tables was written later.
                    if self._conn.execute(
                            "SELECT 1 FROM chap_meta WHERE workspace = ?",
                            (r["id"],)).fetchone() is not None:
                        continue
                    try:
                        data = json.loads(r["data"])
                    except Exception:
                        state["unreadable"].append(r["id"])
                        continue
                    change = change_from_record(WorkspaceRecord(
                        id=r["id"], data=data, version=r["version"],
                        updated_at=r["updated_at"]))
                    change.version = len(change.entries)
                    self._write(change)
                kept = f"{_LEGACY_TABLE}_v1"
                n = 2
                while self._has_name(kept):
                    kept = f"{_LEGACY_TABLE}_v1_{n}"
                    n += 1
                # With legacy_alter_table on, a view or trigger of the
                # application's own that reads the old table keeps reading it
                # by name, so it reads the view below and stays current.
                (legacy,) = self._conn.execute("PRAGMA legacy_alter_table").fetchone()
                self._conn.execute("PRAGMA legacy_alter_table = ON")
                try:
                    self._conn.execute(
                        f"ALTER TABLE {_LEGACY_TABLE} RENAME TO {kept}")
                finally:
                    self._conn.execute(
                        f"PRAGMA legacy_alter_table = {'ON' if legacy else 'OFF'}")
                # The old index moved with the table. Without it, a release
                # before 0.3.2 that opens this file tries to index the view and
                # stops with an error, which is better than starting with
                # nothing loaded.
                self._conn.execute(
                    "DROP INDEX IF EXISTS idx_chap_workspaces_updated_at")
                state["kept"] = kept
            if not self._has_name(_LEGACY_TABLE):
                self._conn.execute(_LEGACY_VIEW)

        self._transaction(move)
        for id in state["unreadable"]:
            _log.warning("workspace %s could not be read during the upgrade; "
                         "its row is kept, unchanged, in %s", id, state["kept"])

    # -- writes ------------------------------------------------------------

    def _write(self, change: StoreChange) -> None:
        ws = change.id
        if change.replace:
            self._drop(ws)
            base = 0
        else:
            last = self._conn.execute(
                "SELECT MAX(seq) AS last FROM chap_entries WHERE workspace = ?",
                (ws,)).fetchone()["last"]
            held = 0 if last is None else last + 1
            if held != change.base:
                raise StoreConflictError(ws, held, change.base)
            base = change.base
        self._conn.executemany(
            "INSERT INTO chap_entries (workspace, seq, data) VALUES (?, ?, ?)",
            [(ws, base + i, _dumps(e)) for i, e in enumerate(change.entries)])
        for collection, items in change.objects.items():
            for key, value in items.items():
                if value is None:
                    self._conn.execute(
                        "DELETE FROM chap_objects "
                        "WHERE workspace = ? AND collection = ? AND id = ?",
                        (ws, collection, key))
                else:
                    self._conn.execute(
                        "INSERT INTO chap_objects (workspace, collection, id, data) "
                        "VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(workspace, collection, id) "
                        "DO UPDATE SET data = excluded.data",
                        (ws, collection, key, _dumps(value)))
        self._conn.execute(
            "INSERT INTO chap_meta (workspace, data, version, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(workspace) DO UPDATE SET "
            "  data       = excluded.data, "
            "  version    = excluded.version, "
            "  updated_at = excluded.updated_at",
            (ws, _dumps(change.meta), change.version, change.updated_at))

    def _drop(self, id: str) -> None:
        for table in ("chap_meta", "chap_entries", "chap_objects"):
            self._conn.execute(f"DELETE FROM {table} WHERE workspace = ?", (id,))

    # -- the Store interface ---------------------------------------------

    def load(self) -> list[WorkspaceRecord]:
        # One read transaction, so a write by another process in the middle
        # of the read cannot leave the log and the objects out of step.
        self._conn.execute("BEGIN")
        try:
            return self._read_all()
        finally:
            # A read changes nothing, so ending it is all that is needed.
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")

    def _read_all(self) -> list[WorkspaceRecord]:
        # A workspace with any unreadable row is skipped whole, with a
        # warning, so the others still load: restoring part of a workspace
        # would put a log beside objects it does not explain.
        broken: dict[str, str] = {}
        entries: dict[str, list] = {}
        for r in self._conn.execute(
                "SELECT workspace, seq, data FROM chap_entries "
                "ORDER BY workspace, seq"):
            ws = r["workspace"]
            if ws in broken:
                continue
            got = entries.setdefault(ws, [])
            if r["seq"] != len(got):
                broken[ws] = f"audit entry {len(got)} is missing"
                continue
            try:
                got.append(json.loads(r["data"]))
            except Exception:
                broken[ws] = f"audit entry {r['seq']} is unreadable"
        objects: dict[str, list] = {}
        for r in self._conn.execute(
                "SELECT workspace, collection, id, data FROM chap_objects "
                "ORDER BY rowid"):
            ws = r["workspace"]
            if ws in broken:
                continue
            try:
                objects.setdefault(ws, []).append(
                    (r["collection"], r["id"], json.loads(r["data"])))
            except Exception:
                broken[ws] = f"{r['collection']} {r['id']} is unreadable"
        out: list[WorkspaceRecord] = []
        for r in self._conn.execute(
                "SELECT workspace, data, version, updated_at FROM chap_meta"):
            ws = r["workspace"]
            meta = None
            if ws not in broken:
                try:
                    meta = json.loads(r["data"])
                except Exception:
                    broken[ws] = "its own fields are unreadable"
            if not isinstance(meta, dict):
                _log.warning("workspace %s was not loaded: %s", ws,
                             broken.get(ws, "its own fields are unreadable"))
                continue
            data = assemble_snapshot(meta, entries.get(ws, []),
                                     objects.get(ws, []))
            data["id"] = ws
            out.append(WorkspaceRecord(id=ws, data=data, version=r["version"],
                                       updated_at=r["updated_at"]))
        return out

    def save(self, record: WorkspaceRecord) -> None:
        self._transaction(self._write, change_from_record(record))

    def apply(self, change: StoreChange) -> None:
        self._transaction(self._write, change)

    def delete(self, id: str) -> None:
        self._transaction(self._drop, id)

    def close(self) -> None:
        self._conn.close()


__all__ = ["SqliteStore"]
