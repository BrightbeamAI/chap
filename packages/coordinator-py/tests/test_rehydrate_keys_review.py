"""
Regression: _rehydrate_workspace reconstructs Member.keys as KeyRecord objects
and Task.review as a ReviewState, so a persisted workspace is usable after a
restart -- an in-flight review can still be decided instead of raising on a
raw dict. Guards the 0.2.7 fix.
"""
from __future__ import annotations

from dataclasses import asdict

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.coordinator import _rehydrate_workspace
from chap_coordinator.types import KeyRecord, ReviewState


def _built():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human",
      jwks={"keys": [{"kid": "k1", "x": "K"}]})
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    tid = s("task.create", workspace="w", **{"from": "human:a"},
            kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    s("review.request", workspace="w", **{"from": "agent:b"},
      task_id=tid, to=["human:a"], artefact={"v": 1})
    return c, s, tid


def test_member_keys_rehydrate_to_keyrecord():
    c, _, _ = _built()
    rt = _rehydrate_workspace(asdict(c.get_workspace("w")))
    assert all(isinstance(k, KeyRecord) for k in rt.members["human:a"].keys)


def test_task_review_rehydrates_to_reviewstate():
    c, _, tid = _built()
    rt = _rehydrate_workspace(asdict(c.get_workspace("w")))
    assert isinstance(rt.tasks[tid].review, ReviewState)


def test_in_review_task_is_decidable_after_restore():
    c, s, tid = _built()
    c.workspaces["w"] = _rehydrate_workspace(asdict(c.get_workspace("w")))
    r = s("decide.approve", workspace="w", **{"from": "human:a"}, task_id=tid)
    assert r["result"]["state"] == "completed"
