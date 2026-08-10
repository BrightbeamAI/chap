"""
Regression: task.create with a repeated idempotency_key returns the original task
and does not create -- or record -- a duplicate. Makes at-least-once redelivery
safe for consumers that capture pipeline decisions into a chain.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _ws():
    c = Coordinator(CoordinatorOptions())
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})
    c.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                "params": {"workspace": "w", "from": "agent:bot", "type": "agent"}})
    return c


def _create(c, key=None):
    params = {"workspace": "w", "from": "agent:bot", "kind": "k",
              "input": {}, "assignee": "agent:bot"}
    if key is not None:
        params["idempotency_key"] = key
    return c.dispatch({"jsonrpc": "2.0", "id": "t", "method": "task.create",
                       "params": params})


def test_repeated_idempotency_key_returns_same_task_without_duplicate():
    c = _ws()
    r1 = _create(c, "cap-42")
    r2 = _create(c, "cap-42")
    assert r1["result"]["task_id"] == r2["result"]["task_id"]

    ws = c.workspaces["w"]
    assert len(ws.tasks) == 1
    creates = [e for e in ws.audit if e.envelope.get("method") == "task.create"]
    assert len(creates) == 1


def test_no_key_creates_distinct_tasks():
    c = _ws()
    a = _create(c)
    b = _create(c)
    assert a["result"]["task_id"] != b["result"]["task_id"]
    assert len(c.workspaces["w"].tasks) == 2
