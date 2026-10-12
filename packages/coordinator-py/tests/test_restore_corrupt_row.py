"""
Regression: a single unreadable row must not discard every other workspace.
SqliteStore.load() skips the workspace whose row cannot be read and returns the
rest, so one bad row does not wipe the store on restart. Guards the 0.2.9 fix,
and the same for each table the store writes since 0.3.2.
"""
from __future__ import annotations

import logging

from chap_coordinator.storage.sqlite import SqliteStore
from chap_coordinator.storage.store import WorkspaceRecord


def test_load_skips_a_workspace_with_a_corrupt_row(caplog):
    store = SqliteStore(":memory:")
    for w in ("w1", "w2", "w3", "w4", "w5"):
        store.save(WorkspaceRecord(
            id=w, data={"id": w, "audit": [{"seq": 0}, {"seq": 1}],
                        "tasks": {"t1": {"id": "t1"}}},
            version=2, updated_at="t"))
    run = store._conn.execute
    run("UPDATE chap_meta SET data = '{bad json' WHERE workspace = 'w2'")
    run("UPDATE chap_entries SET data = '{bad json' WHERE workspace = 'w3' AND seq = 1")
    run("UPDATE chap_objects SET data = '{bad json' WHERE workspace = 'w4'")
    run("DELETE FROM chap_entries WHERE workspace = 'w5' AND seq = 0")
    with caplog.at_level(logging.WARNING):
        assert sorted(r.id for r in store.load()) == ["w1"]
    warned = " ".join(r.getMessage() for r in caplog.records)
    for w in ("w2", "w3", "w4", "w5"):
        assert f"workspace {w} was not loaded" in warned
