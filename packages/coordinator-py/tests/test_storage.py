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
    # load returns the snapshot shape the Coordinator restores from: the
    # saved fields, the collections and the log.
    assert loaded[0].data["k"] == 1
    assert loaded[0].data["audit"] == [] and loaded[0].data["tasks"] == {}


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
    assert loaded[0].data["k"] == 2
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
    """A broken store must not bring down the Coordinator. Its call is
    answered as failed, or, under store_errors="log", as it was."""
    class BrokenStore:
        def load(self):       return []
        def save(self, rec):  raise RuntimeError("nope")
        def delete(self, id): pass
        def close(self):      pass

    create = {"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
              "params": {"workspace": "wsp_brk"}}
    coord = Coordinator(CoordinatorOptions(store=BrokenStore(), on_store_error=lambda e, r: None))
    r = coord.dispatch(dict(create))
    assert r["error"] == {"code": -32603, "message": "Internal error", "data": {"reason": "store"}}
    # The store holds no such workspace, and neither does the coordinator.
    assert "wsp_brk" not in coord.workspaces
    logged = Coordinator(CoordinatorOptions(store=BrokenStore(), store_errors="log",
                                            on_store_error=lambda e, r: None))
    assert logged.dispatch(dict(create))["result"]["workspace"] == "wsp_brk"


# ---- A second writer (#69) -----------------------------------------
#
# The Coordinator is single-writer. Each write names the number of audit
# entries it expects the store to hold, and the shipped stores refuse a write
# that does not match, so a second writer is refused and reported, and what is
# stored is kept. Before 0.3.2 the stale writer replaced the store's copy and
# the other writer's entries vanished without a word.

def _pair(store, errors):
    def coord():
        return Coordinator(CoordinatorOptions(
            deterministic_ids=True, deterministic_clock=True,
            enable_chain=True, store=store,
            on_store_error=lambda err, rec: errors.append((err, rec))))
    return coord


def _join(c, n, who):
    return c.dispatch({"jsonrpc": "2.0", "id": str(n), "method": "participant.join",
                       "params": {"workspace": "w", "from": who,
                                  "type": "human", "role": "reviewer"}})


def test_a_coordinator_reloads_what_another_wrote_before_its_next_call():
    store, errors = MemoryStore(), []
    coord = _pair(store, errors)
    a = coord()
    a.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    _join(a, 2, "human:alice")

    b = coord()   # loads what A wrote
    _join(b, 3, "human:bob")

    # A held an older state; it reloads, and writes on top of B's entry.
    assert "result" in _join(a, 4, "human:carol")
    assert errors == []
    assert "human:bob" in a.get_workspace("w").members

    survivor = coord()
    audit = survivor.get_workspace("w").audit
    assert any("human:bob" in str(e.envelope) for e in audit)
    assert any("human:carol" in str(e.envelope) for e in audit)
    r = survivor.dispatch({"jsonrpc": "2.0", "id": "5", "method": "audit.verify_chain",
                           "params": {"workspace": "w", "from": "human:alice"}})
    assert r["result"]["ok"] is True


def test_a_write_another_writer_got_to_first_is_answered_as_failed_and_may_be_sent_again():
    # The call ran a second time on its own, so whatever it did outside the
    # workspace, a verifier it called or a hook, happened twice, and the
    # TypeScript coordinator answered the same race as failed.
    from chap_coordinator.storage.store import StoreConflictError
    store, errors = MemoryStore(), []
    coord = _pair(store, errors)
    a = coord()
    a.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    _join(a, 2, "human:alice")
    b = coord()
    # B writes between A's check of the store and A's write.
    version = store.version

    def racing(ws_id):
        held = version(ws_id)
        store.version = version
        _join(b, 3, "human:bob")
        return held
    store.version = racing
    assert _join(a, 4, "human:carol")["error"] == {
        "code": -32603, "message": "Internal error", "data": {"reason": "store"}}
    assert len(errors) == 1
    err, rec = errors[0]
    assert isinstance(err, StoreConflictError)
    assert (err.held, err.base) == (3, 2)
    assert rec == {"id": "w", "version": 3}
    # A holds what the store holds, Bob included, and the call sent again
    # takes effect.
    assert "human:bob" in a.get_workspace("w").members
    assert "result" in _join(a, 5, "human:carol")
    survivor = coord().get_workspace("w")
    assert {"human:bob", "human:carol"} <= set(survivor.members)
    assert len(survivor.audit) == 4
