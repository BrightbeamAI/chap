"""
handoff.accept takes only tasks the proposer still holds (29 October
review). A task handed elsewhere after the proposal was moved again by the
accept, away from whoever held it. Mirrors handoff_ownership.test.ts.
"""
from __future__ import annotations

import copy

from chap_coordinator import Coordinator, CoordinatorOptions


def _ready():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))

    def send(method, frm="human:alice", **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": frm, **params}})

    send("workspace.create", profiles=["core/1.0", "review/1.0", "handoff/1.0"])
    for who in ("human:alice", "human:bob", "human:carol"):
        send("participant.join", who, type="human")
    ids = [send("task.create", kind="k", input={}, assignee="human:alice")["result"]["task_id"]
           for _ in range(2)]
    to_bob = send("handoff.propose", to="human:bob",
                  tasks=[{"task_id": t} for t in ids])["result"]["handoff_id"]
    to_carol = send("handoff.propose", to="human:carol",
                    tasks=[{"task_id": ids[0]}])["result"]["handoff_id"]
    return c, send, ids, to_bob, to_carol


def _plain(c):
    return copy.deepcopy(c._snapshot_workspace(c.workspaces["w"]).data)


def test_a_task_the_proposer_no_longer_holds_is_refused_at_accept_and_nothing_changes():
    c, send, ids, to_bob, to_carol = _ready()
    assert "error" not in send("handoff.accept", "human:carol", handoff_id=to_carol)
    before = _plain(c)
    r = send("handoff.accept", "human:bob", handoff_id=to_bob)
    assert r["error"] == {"code": -32050,
                          "message": f"Task {ids[0]} is not currently assigned to human:alice"}
    assert c.workspaces["w"].tasks[ids[0]].assignee == "human:carol"
    # The refusal is recorded, as a member's refused call is; nothing else changes.
    after = _plain(c)
    after["audit"].pop()
    assert after == before


def test_the_tasks_the_proposer_still_holds_can_be_accepted_alone():
    c, send, ids, to_bob, to_carol = _ready()
    send("handoff.accept", "human:carol", handoff_id=to_carol)
    r = send("handoff.accept", "human:bob", handoff_id=to_bob, accepted_task_ids=[ids[1]])
    assert r["result"]["task_ids"] == [ids[1]]
    assert c.workspaces["w"].tasks[ids[1]].assignee == "human:bob"
