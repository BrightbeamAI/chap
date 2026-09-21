"""Empty handoff selections are refused without recording a no-op transfer."""
from __future__ import annotations

import copy

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions


def _ready():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, enable_chain=True))

    def send(method, **params):
        return coord.dispatch({
            "jsonrpc": "2.0", "id": method, "method": method,
            "params": {"workspace": "w", "from": "human:alice", **params},
        })

    send("workspace.create")
    send("participant.join", type="human")
    send("participant.join", **{"from": "human:bob"}, type="human")
    task_ids = [send("task.create", kind="handoff", input={},
                     assignee="human:alice")["result"]["task_id"] for _ in range(2)]
    handoff_id = send("handoff.propose", to="human:bob",
                      tasks=[{"task_id": tid} for tid in task_ids])["result"]["handoff_id"]
    return coord, send, task_ids, handoff_id


def test_empty_acceptance_is_refused_without_state_or_audit_mutation():
    coord, send, task_ids, handoff_id = _ready()
    before = copy.deepcopy(coord.get_workspace("w"))
    reply = send("handoff.accept", **{"from": "human:bob"},
                 handoff_id=handoff_id, accepted_task_ids=[])
    assert reply["error"]["code"] == -32602
    assert "result" not in reply
    # Includes the handoff state, task ownership/history, audit and chain head.
    assert coord.get_workspace("w") == before
    # Refusal leaves the proposal available for a later genuine acceptance.
    retry = send("handoff.accept", **{"from": "human:bob"}, handoff_id=handoff_id)
    assert retry["result"]["task_ids"] == task_ids


@pytest.mark.parametrize("partial", [False, True], ids=["omitted-accepts-all", "nonempty-subset"])
def test_omitted_and_nonempty_acceptance_keep_existing_semantics(partial):
    coord, send, task_ids, handoff_id = _ready()
    selected = task_ids[:1] if partial else task_ids
    params = {"accepted_task_ids": selected} if partial else {}
    reply = send("handoff.accept", **{"from": "human:bob"},
                 handoff_id=handoff_id, **params)
    assert reply["result"]["task_ids"] == selected
    ws = coord.get_workspace("w")
    for tid in task_ids:
        assert ws.tasks[tid].assignee == ("human:bob" if tid in selected else "human:alice")
    assert ws.handoffs[handoff_id].accepted_task_ids == selected
