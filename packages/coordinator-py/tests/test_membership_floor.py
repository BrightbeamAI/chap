"""
Regression: task.create / task.update require the caller to be a workspace
member; workspace.set_profiles requires the admin role; audit.read and
workspace.describe are transport-delegated by default but can be gated with
require_read_membership.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _member_ws(**opts):
    c = Coordinator(CoordinatorOptions(**opts))
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w", "profiles": ["core/1.0"]}})
    c.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "agent:bot", "type": "agent"}})
    return c


def test_task_create_requires_membership():
    c = _member_ws()
    r = c.dispatch({"jsonrpc": "2.0", "id": "t", "method": "task.create",
                    "params": {"workspace": "w", "from": "human:stranger",
                               "kind": "k", "input": {}, "assignee": "agent:bot"}})
    assert r["error"]["code"] == -32011


def test_task_update_requires_membership():
    c = _member_ws()
    tc = c.dispatch({"jsonrpc": "2.0", "id": "c", "method": "task.create",
                     "params": {"workspace": "w", "from": "agent:bot",
                                "kind": "k", "input": {}, "assignee": "agent:bot"}})
    tid = tc["result"]["task_id"]
    r = c.dispatch({"jsonrpc": "2.0", "id": "u", "method": "task.update",
                    "params": {"workspace": "w", "from": "human:stranger",
                               "task_id": tid, "state": "in_progress"}})
    assert r["error"]["code"] == -32011


def test_set_profiles_requires_admin():
    c = Coordinator(CoordinatorOptions())
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    c.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:alice", "type": "human"}})
    c.dispatch({"jsonrpc": "2.0", "id": "3", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:admin", "type": "human",
                           "role": "admin"}})

    def set_profiles(sender):
        return c.dispatch({"jsonrpc": "2.0", "id": "s", "method": "workspace.set_profiles",
                           "params": {"workspace": "w", "from": sender,
                                      "profiles": ["core/1.0", "review/1.0"]}})

    assert set_profiles("human:alice")["error"]["code"] == -32011
    assert "result" in set_profiles("human:admin")


def test_reads_transport_delegated_by_default():
    c = _member_ws()
    assert "result" in c.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read",
                                   "params": {"workspace": "w"}})
    assert "result" in c.dispatch({"jsonrpc": "2.0", "id": "d", "method": "workspace.describe",
                                   "params": {"workspace": "w"}})


def test_reads_gateable_with_require_read_membership():
    c = _member_ws(require_read_membership=True)

    def read(sender):
        p = {"workspace": "w"}
        if sender is not None:
            p["from"] = sender
        return c.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read", "params": p})

    assert read("human:stranger")["error"]["code"] == -32011
    assert "result" in read("agent:bot")
