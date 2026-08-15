"""
Regression: decide.override records the artefact under review as its base, not a
caller-supplied based_on_artefact. A fabricated "before" is ignored. Guards the
0.2.9 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _ready_override():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human")
    tid = s("task.create", workspace="w", **{"from": "human:a"},
            kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    s("review.request", workspace="w", **{"from": "agent:b"},
      task_id=tid, to=["human:a"], artefact={"real": "pending"})
    return c, s, tid


def test_override_ignores_caller_supplied_based_on():
    c, s, tid = _ready_override()
    r = s("decide.override", workspace="w", **{"from": "human:a"}, task_id=tid,
          based_on_artefact={"FORGED": "before"},
          diff=[{"op": "add", "path": "/x", "value": 1}], rationale="x")
    assert r["result"]["applied"] == {"real": "pending", "x": 1}
    ov = list(c.get_workspace("w").overrides.values())[-1]
    assert ov.based_on_artefact == {"real": "pending"}
