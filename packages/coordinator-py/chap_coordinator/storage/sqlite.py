"""
SQLite-backed store using the stdlib `sqlite3` module.

Schema (compatible with the TypeScript SqliteStore so audit files
written by one implementation can be read by the other):

    CREATE TABLE chap_workspaces (
      id         TEXT PRIMARY KEY,
      data       TEXT NOT NULL,    -- json.dumps of snapshot
      version    INTEGER NOT NULL,
      updated_at TEXT NOT NULL     -- ISO-8601
    );

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
import sqlite3
from typing import Any

from .store import Store, WorkspaceRecord


_SCHEMA = """
CREATE TABLE IF NOT EXISTS chap_workspaces (
    id         TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chap_workspaces_updated_at
    ON chap_workspaces (updated_at);
"""


class SqliteStore:
    """
    SQLite-backed implementation of the `Store` Protocol.

    Wire-compatible with the TypeScript `SqliteStore`: both write the
    same schema, both round-trip the same JSON payload shape. A database
    file produced by one implementation can be read by the other.
    """

    def __init__(self, path: str, *, wal: bool = True) -> None:
        # `check_same_thread=False` allows the Coordinator's persist hook
        # to call save() from any thread. SQLite serialises writes
        # internally, so this is safe for the single-writer pattern the
        # Coordinator uses.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        # Returning rows as dict-like sqlite3.Row objects is slightly
        # nicer than index access in load().
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
        self._conn.commit()

    def load(self) -> list[WorkspaceRecord]:
        rows = self._conn.execute(
            "SELECT id, data, version, updated_at FROM chap_workspaces"
        ).fetchall()
        return [
            WorkspaceRecord(
                id=row["id"],
                data=json.loads(row["data"]),
                version=row["version"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def save(self, record: WorkspaceRecord) -> None:
        self._conn.execute(
            "INSERT INTO chap_workspaces (id, data, version, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  data       = excluded.data, "
            "  version    = excluded.version, "
            "  updated_at = excluded.updated_at",
            (
                record.id,
                json.dumps(record.data, sort_keys=True),
                record.version,
                record.updated_at,
            ),
        )
        self._conn.commit()

    def delete(self, id: str) -> None:
        self._conn.execute("DELETE FROM chap_workspaces WHERE id = ?", (id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


__all__ = ["SqliteStore"]
