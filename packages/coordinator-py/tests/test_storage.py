"""
Tests for the Python SqliteStore.

Mirrors the TypeScript SqliteStore test surface. Stdlib sqlite3 means
these tests run on every supported Python version without external
dependencies.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.store import (
    MemoryStore, Store, WorkspaceRecord,
)
from chap_coordinator.storage.sqlite import SqliteStore


# ---- MemoryStore (default) ---------------------------------------


def test_memory_store_is_the_default():
    """A bare Coordinator runs with no store; new state is in-memory."""
    coord = Coordinator()
    assert coord.options.store is None


def test_memory_store_save_load_roundtrip():
    store = MemoryStore()
    rec = WorkspaceRecord(id="wsp_a", data={"k": 1}, version=1,
                          updated_at="2026-01-01T00:00:00.000Z")
    store.save(rec)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].id == "wsp_a"
    assert loaded[0].data == {"k": 1}


def test_memory_store_satisfies_store_protocol():
    assert isinstance(MemoryStore(), Store)


# ---- SqliteStore -------------------------------------------------


def test_sqlite_store_creates_schema():
    store = SqliteStore(":memory:")
    # Schema should exist; the table is queryable even when empty.
    rows = store.load()
    assert rows == []
    store.close()


def test_sqlite_store_save_load_roundtrip():
    store = SqliteStore(":memory:")
    rec = WorkspaceRecord(
        id="wsp_x",
        data={"audit": [{"seq": 0, "envelope": {"method": "workspace.create"}}]},
        version=1,
        updated_at="2026-01-01T00:00:00.000Z",
    )
    store.save(rec)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].id == "wsp_x"
    assert loaded[0].data["audit"][0]["envelope"]["method"] == "workspace.create"
    store.close()


def test_sqlite_store_upsert_on_repeat_save():
    store = SqliteStore(":memory:")
    rec1 = WorkspaceRecord(id="wsp_y", data={"k": 1}, version=1,
                           updated_at="2026-01-01T00:00:00.000Z")
    rec2 = WorkspaceRecord(id="wsp_y", data={"k": 2}, version=2,
                           updated_at="2026-01-02T00:00:00.000Z")
    store.save(rec1)
    store.save(rec2)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].data == {"k": 2}
    assert loaded[0].version == 2
    store.close()


def test_sqlite_store_delete():
    store = SqliteStore(":memory:")
    store.save(WorkspaceRecord(id="wsp_z", data={}, version=1,
                                updated_at="2026-01-01T00:00:00.000Z"))
    store.delete("wsp_z")
    assert store.load() == []
    store.close()


def test_sqlite_store_persists_across_connections(tmp_path):
    """Closing and reopening the same path returns the saved data."""
    db_path = str(tmp_path / "test.db")
    store1 = SqliteStore(db_path)
    store1.save(WorkspaceRecord(
        id="wsp_p", data={"persistent": True}, version=1,
        updated_at="2026-01-01T00:00:00.000Z",
    ))
    store1.close()

    store2 = SqliteStore(db_path)
    loaded = store2.load()
    assert len(loaded) == 1
    assert loaded[0].data["persistent"] is True
    store2.close()


# ---- Coordinator integration -------------------------------------


def test_coordinator_persists_to_sqlite_store(tmp_path):
    """End-to-end: dispatch envelopes, restart, see the same chain."""
    db_path = str(tmp_path / "chap.db")

    coord = Coordinator(CoordinatorOptions(store=SqliteStore(db_path)))
    coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "workspace.create",
        "params": {"workspace": "wsp_persist", "profiles": ["core/1.0"]},
    })
    coord.dispatch({
        "jsonrpc": "2.0", "id": "2",
        "method": "participant.join",
        "params": {"workspace": "wsp_persist", "from": "human:alice", "type": "human"},
    })
    n_before = len(coord.workspaces["wsp_persist"].audit)
    coord.options.store.close()

    # Restart with a fresh Coordinator pointing at the same DB
    coord2 = Coordinator(CoordinatorOptions(store=SqliteStore(db_path)))
    assert "wsp_persist" in coord2.workspaces
    ws = coord2.workspaces["wsp_persist"]
    assert len(ws.audit) == n_before
    # Subsequent dispatches append to the rehydrated chain
    coord2.dispatch({
        "jsonrpc": "2.0", "id": "3",
        "method": "participant.join",
        "params": {"workspace": "wsp_persist", "from": "agent:bot", "type": "agent"},
    })
    assert len(coord2.workspaces["wsp_persist"].audit) == n_before + 1
    coord2.options.store.close()


def test_persistence_failures_do_not_break_dispatch():
    """A broken store must not bring down the Coordinator."""
    class BrokenStore:
        def load(self):       return []
        def save(self, rec):  raise RuntimeError("nope")
        def delete(self, id): pass
        def close(self):      pass

    coord = Coordinator(CoordinatorOptions(store=BrokenStore()))
    # Dispatch should succeed even though save() throws every time.
    r = coord.dispatch({
        "jsonrpc": "2.0", "id": "1",
        "method": "workspace.create",
        "params": {"workspace": "wsp_brk"},
    })
    assert "result" in r
    assert r["result"]["workspace"] == "wsp_brk"


# ---- Single-writer limitation (#69) ---------------------------------
#
# These characterise a documented limitation rather than desired behaviour.
# The Coordinator is single-writer; the store interface performs no
# compare-and-swap. If that ever changes, these tests should fail and be
# rewritten deliberately, which is the point of pinning it here.

def test_multi_writer_silently_loses_entries():
    """Two Coordinators on one store: the stale writer wins and entries vanish."""
    store = MemoryStore()

    def coord():
        return Coordinator(CoordinatorOptions(
            deterministic_ids=True, deterministic_clock=True,
            enable_chain=True, store=store))

    a = coord()
    a.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    a.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:alice",
                           "type": "human", "role": "owner"}})

    b = coord()   # loads A's snapshot
    b.dispatch({"jsonrpc": "2.0", "id": "3", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:bob",
                           "type": "human", "role": "reviewer"}})

    # A still holds its older snapshot and writes again: last write wins.
    a.dispatch({"jsonrpc": "2.0", "id": "4", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:carol",
                           "type": "human", "role": "reviewer"}})

    survivor = coord()
    audit = survivor.get_workspace("w").audit
    assert not any("human:bob" in str(e.envelope) for e in audit), (
        "bob's join is expected to be lost under multi-writer; if this now "
        "survives, the store contract has gained real concurrency control and "
        "SPECIFICATION.md section 10.3 needs updating"
    )


def test_verification_cannot_detect_the_loss():
    """The surviving chain is internally consistent, so verify reports ok."""
    store = MemoryStore()

    def coord():
        return Coordinator(CoordinatorOptions(
            deterministic_ids=True, deterministic_clock=True,
            enable_chain=True, store=store))

    a = coord()
    a.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    a.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:alice",
                           "type": "human", "role": "owner"}})
    b = coord()
    b.dispatch({"jsonrpc": "2.0", "id": "3", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:bob",
                           "type": "human", "role": "reviewer"}})
    a.dispatch({"jsonrpc": "2.0", "id": "4", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:carol",
                           "type": "human", "role": "reviewer"}})

    survivor = coord()
    r = survivor.dispatch({"jsonrpc": "2.0", "id": "5", "method": "audit.verify_chain",
                           "params": {"workspace": "w", "from": "human:alice"}})
    assert r["result"]["ok"] is True, (
        "verification proves internal consistency, not completeness; this is "
        "why external anchoring via audit-scitt/1.0 exists"
    )
