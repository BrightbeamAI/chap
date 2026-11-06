"""
conformance/store-vectors.json: a store the TypeScript coordinator wrote
restores here to the state this coordinator reached from the same calls.

The TypeScript coordinator names a history entry's sender ``from``, keeps a
route decision's own fields at the top beside its ``kind``, and leaves out a
field with no value, and this coordinator could not restore any of it: the
workspace was skipped with a warning. Mirrors store_cross_language.test.ts,
which restores the Python snapshot.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.store import MemoryStore, WorkspaceRecord

VECTORS = json.loads((Path(__file__).resolve().parents[3]
                      / "conformance" / "store-vectors.json").read_text("utf-8"))


def _options(**more):
    return CoordinatorOptions(deterministic_ids=True, deterministic_clock=True,
                              enable_chain=True, **more)


def _native():
    c = Coordinator(_options())
    for e in VECTORS["envelopes"]:
        c.dispatch(copy.deepcopy(e))
    return c


def _from_typescript():
    store = MemoryStore()
    store.save(WorkspaceRecord(id="w", data=copy.deepcopy(VECTORS["typescript"]),
                               version=len(VECTORS["typescript"]["audit"]), updated_at="t"))
    return Coordinator(_options(store=store))


def _plain(c):
    return json.loads(json.dumps(c._snapshot_workspace(c.workspaces["w"]).data, default=str))


def _without_nulls(value):
    """A snapshot with its null fields left out. A deliberation vote this
    coordinator makes holds a null weight and comment where the TypeScript
    one leaves them out, and both read a missing field as null."""
    if isinstance(value, dict):
        return {k: _without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_nulls(v) for v in value]
    return value


def test_this_coordinators_snapshot_is_the_vectors():
    assert _plain(_native()) == VECTORS["python"]


def test_a_store_the_typescript_coordinator_wrote_restores_to_the_state_this_one_reached():
    assert _without_nulls(_plain(_from_typescript())) == _without_nulls(_plain(_native()))


def test_the_restored_workspace_carries_on_as_this_ones_would():
    loaded, own = _from_typescript(), _native()
    task = next(t for t in own.workspaces["w"].tasks.values() if t.state == "review_requested")
    for c in (loaded, own):
        author = c.dispatch({"jsonrpc": "2.0", "id": "a", "method": "decide.approve",
                             "params": {"workspace": "w", "from": "agent:b", "task_id": task.id}})
        assert author["error"]["code"] == -32011
    approve = {"jsonrpc": "2.0", "id": "d", "method": "decide.approve",
               "params": {"workspace": "w", "from": "human:c", "task_id": task.id}}
    assert loaded.dispatch(copy.deepcopy(approve)) == own.dispatch(copy.deepcopy(approve))
    assert loaded.workspaces["w"].chain_head == own.workspaces["w"].chain_head
