"""
Pluggable persistence for the Coordinator.

A Store persists workspace state between process restarts. Coordinators
call `store.save(record)` after every mutation that appends to the audit
chain, and `store.load()` on startup to restore.

Two implementations ship in-tree:
  - MemoryStore  (default, no persistence; existing behaviour)
  - SqliteStore  (real SQLite via the stdlib `sqlite3` module)

Third-party stores (Postgres, Redis, etc.) implement this interface.

Stores hold per-workspace JSON snapshots, not a decomposed relational
model. The Coordinator's snapshot/restore methods produce JSON-safe
payloads; this is fast for workspaces in the low thousands of audit
entries and trades query flexibility for simplicity. Production
deployments needing rich query semantics should project the audit log
into their own analytics store via the `on_audit` listener.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class WorkspaceRecord:
    """One persisted workspace snapshot."""

    id:         str
    data:       Any         # JSON-serialisable payload from Coordinator.snapshot()
    version:    int         # Monotonically increasing for optimistic concurrency
    updated_at: str         # ISO-8601 wall-clock timestamp


@runtime_checkable
class Store(Protocol):
    """
    Pluggable persistence interface.

    Implementations need not be async; the Coordinator awaits whatever
    `save()` returns, so both sync and async stores are supported.
    """

    def load(self) -> list[WorkspaceRecord]:
        """Load all workspace records. Called once on coordinator start."""
        ...

    def save(self, record: WorkspaceRecord) -> None:
        """Persist one workspace record. Called after every mutation."""
        ...

    def delete(self, id: str) -> None:
        """Remove one workspace. Used by workspace.delete (not in v0.2)."""
        ...

    def close(self) -> None:
        """Release resources. Idempotent."""
        ...


class MemoryStore:
    """
    In-memory store. Default behaviour: no persistence, fast tests.
    State lives in a dict; closing it discards everything.
    """

    def __init__(self) -> None:
        self._records: dict[str, WorkspaceRecord] = {}

    def load(self) -> list[WorkspaceRecord]:
        return list(self._records.values())

    def save(self, record: WorkspaceRecord) -> None:
        self._records[record.id] = record

    def delete(self, id: str) -> None:
        self._records.pop(id, None)

    def close(self) -> None:
        self._records.clear()


__all__ = ["Store", "WorkspaceRecord", "MemoryStore"]
