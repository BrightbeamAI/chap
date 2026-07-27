"""
Regression: a single unreadable row must not discard every other workspace.
SqliteStore.load() skips a corrupt row and returns the rest, so one bad blob does
not wipe the store on restart. Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator.storage.sqlite import SqliteStore
from chap_coordinator.storage.store import WorkspaceRecord


def test_load_skips_a_corrupt_row():
    store = SqliteStore(":memory:")
    for w in ("w1", "w2", "w3"):
        store.save(WorkspaceRecord(id=w, data={"id": w}, version=1, updated_at="t"))
    store._conn.execute("UPDATE chap_workspaces SET data='{bad json' WHERE id='w2'")
    store._conn.commit()
    assert sorted(r.id for r in store.load()) == ["w1", "w3"]
