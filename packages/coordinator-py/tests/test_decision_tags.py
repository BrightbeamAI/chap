"""
Regression: decision tags must be a list of strings, per the schema
(schemas/core/chap-task.schema.json). A non-string tag is rejected with PARAMS.
Guards the 0.2.9 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E


def _ready_review():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human")
    tid = s("task.create", workspace="w", **{"from": "human:a"},
            kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    s("review.request", workspace="w", **{"from": "agent:b"},
      task_id=tid, to=["human:a"], artefact={"v": 1})
    return s, tid


def test_decide_rejects_non_string_tags():
    s, tid = _ready_review()
    r = s("decide.approve", workspace="w", **{"from": "human:a"},
          task_id=tid, tags=[{"x": 1}, 123])
    assert r["error"]["code"] == E.PARAMS


def test_override_rejects_non_string_tags():
    s, tid = _ready_review()
    r = s("decide.override", workspace="w", **{"from": "human:a"}, task_id=tid,
          diff=[{"op": "replace", "path": "/v", "value": 2}],
          rationale="x", tags=["ok", 5])
    assert r["error"]["code"] == E.PARAMS


def test_string_tags_accepted():
    s, tid = _ready_review()
    r = s("decide.approve", workspace="w", **{"from": "human:a"},
          task_id=tid, tags=["routine"])
    assert r["result"]["state"] == "completed"
