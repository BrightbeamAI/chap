"""
Regression: dispatch must reject an envelope it cannot canonicalise instead of
letting a handler run and then raising while linking the audit chain. A
non-integer or unsafe-integer number is refused with PARAMS, before any state
changes. Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E


def _ready(**opts):
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, **opts))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human")
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    r = s("task.create", workspace="w", **{"from": "human:a"},
          kind="k", input={}, assignee="agent:b")
    tid = r["result"]["task_id"]
    s("task.update", workspace="w", **{"from": "agent:b"}, task_id=tid, state="in_progress")
    return c, s, tid


def test_non_integer_number_is_rejected_with_params():
    c, s, tid = _ready()
    r = s("task.complete", workspace="w", **{"from": "agent:b"},
          task_id=tid, output={}, confidence=0.86)
    assert "error" in r
    assert r["error"]["code"] == E.PARAMS


def test_unsafe_integer_is_rejected_with_params():
    c, s, tid = _ready()
    r = s("task.complete", workspace="w", **{"from": "agent:b"},
          task_id=tid, output={}, confidence=2 ** 53)
    assert "error" in r
    assert r["error"]["code"] == E.PARAMS


def test_rejected_before_any_state_change():
    c, s, tid = _ready(enable_chain=True)
    before = len(c.get_workspace("w").audit)
    s("task.complete", workspace="w", **{"from": "agent:b"},
      task_id=tid, output={}, confidence=0.5)
    assert c.get_workspace("w").tasks[tid].state == "in_progress"
    assert len(c.get_workspace("w").audit) == before


def test_integer_confidence_still_accepted():
    c, s, tid = _ready()
    r = s("task.complete", workspace="w", **{"from": "agent:b"},
          task_id=tid, output={}, confidence=1)
    assert r["result"]["state"] == "completed"
