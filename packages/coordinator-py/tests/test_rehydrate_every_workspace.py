"""A coordinator started on a store brings back every workspace in it.

The TypeScript coordinator restored record by record and kept only the last
workspace. This holds the Python coordinator to the same test.

The mirror of this file is
packages/coordinator/tests/rehydrate_every_workspace.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.store import MemoryStore


def test_every_workspace_in_the_store_survives_a_restart():
    store = MemoryStore()
    coord = Coordinator(CoordinatorOptions(store=store, deterministic_ids=True,
                                           deterministic_clock=True))
    for w in ("w1", "w2", "w3"):
        coord.dispatch({"jsonrpc": "2.0", "id": f"c-{w}", "method": "workspace.create",
                        "params": {"workspace": w, "from": "human:a"}})
        coord.dispatch({"jsonrpc": "2.0", "id": f"j-{w}", "method": "participant.join",
                        "params": {"workspace": w, "from": "human:a", "type": "human"}})
    restarted = Coordinator(CoordinatorOptions(store=store))
    assert sorted(restarted.workspaces) == ["w1", "w2", "w3"]
    for w in ("w1", "w2", "w3"):
        assert len(restarted.get_workspace(w).audit) == len(coord.get_workspace(w).audit), w
