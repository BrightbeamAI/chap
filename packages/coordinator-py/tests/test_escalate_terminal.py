"""
Regression: escalate.raise requires the caller to be a workspace member and
refuses a terminal task. Per SPECIFICATION.md the transition is "any
non-terminal -> escalated", with terminal states completed/cancelled/superseded.
Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E


def _ready():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human")
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    tid = s("task.create", workspace="w", **{"from": "human:a"},
            kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    return s, tid


def test_non_member_cannot_escalate():
    s, tid = _ready()
    r = s("escalate.raise", workspace="w", **{"from": "ghost:x"},
          original_task_id=tid, new_task={"assignee": "agent:b"})
    assert r["error"]["code"] == E.NOT_AUTHORISED


def test_cannot_escalate_a_completed_task():
    s, tid = _ready()
    s("task.update", workspace="w", **{"from": "agent:b"}, task_id=tid, state="in_progress")
    s("task.complete", workspace="w", **{"from": "agent:b"}, task_id=tid, output={})
    r = s("escalate.raise", workspace="w", **{"from": "human:a"},
          original_task_id=tid, new_task={"assignee": "agent:b"})
    assert "error" in r
    assert "terminal" in r["error"]["message"]


def test_member_can_escalate_an_active_task():
    s, tid = _ready()
    r = s("escalate.raise", workspace="w", **{"from": "human:a"},
          original_task_id=tid, new_task={"assignee": "agent:b"})
    assert r["result"]["escalated_from"] == tid
