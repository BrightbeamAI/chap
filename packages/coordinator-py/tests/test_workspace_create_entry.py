"""
A workspace.create that names no workspace is recorded in the one it makes.

The call was recorded only under a workspace its params named, so a workspace
whose id the coordinator chose began with no entry. Nothing was written to the
store until a later call added one, and a coordinator restarted before then
had lost the workspace.

Mirrors packages/coordinator/tests/workspace_create_entry.test.ts.
"""
from __future__ import annotations

import copy

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.store import MemoryStore

CREATE = {"jsonrpc": "2.0", "id": "c1", "method": "workspace.create",
          "params": {"profiles": ["core/1.0"]}}


def test_the_call_is_the_workspaces_first_entry():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))
    ws_id = c.dispatch(copy.deepcopy(CREATE))["result"]["workspace"]
    audit = c.workspaces[ws_id].audit
    assert len(audit) == 1
    assert audit[0].envelope == CREATE


def test_the_workspace_survives_a_restart_on_its_store():
    store = MemoryStore()
    c = Coordinator(CoordinatorOptions(store=store, deterministic_ids=True,
                                       deterministic_clock=True))
    ws_id = c.dispatch(copy.deepcopy(CREATE))["result"]["workspace"]
    again = Coordinator(CoordinatorOptions(store=store))
    assert ws_id in again.workspaces
    assert len(again.workspaces[ws_id].audit) == 1
