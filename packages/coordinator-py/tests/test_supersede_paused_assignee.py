"""
Regression (#153): control.supersede applies the participant-paused check.

task.create refuses a paused assignee. Superseding creates a task too, and
skipped the check, so a successor could be handed to a participant whose work
control.pause had stopped.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions

PROFILES = ["core/1.0", "review/1.0", "control/1.0"]


def _ready():
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, default_profiles=PROFILES))

    def send(method, sender="human:a", **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                               "params": {"workspace": "w", "from": sender, **params}})

    send("workspace.create", profiles=PROFILES)
    for uri, kind in (("human:a", "human"), ("agent:b", "agent"), ("agent:c", "agent")):
        send("participant.join", sender=uri, type=kind)
    return coord, send


def test_a_successor_cannot_be_handed_to_a_paused_participant():
    coord, send = _ready()
    task_id = send("task.create", kind="draft", input={}, assignee="agent:b")["result"]["task_id"]
    send("control.pause", scope="participant", participant_uri="agent:c", reason="hold")

    refused = send("control.supersede", task_id=task_id, reason="redo",
                   successor_task={"kind": "draft", "input": {}, "assignee": "agent:c"})
    assert "error" in refused
    assert "paused" in refused["error"]["message"]
    # The same refusal task.create gives, so the two agree.
    direct = send("task.create", kind="draft", input={}, assignee="agent:c")
    assert direct["error"]["code"] == refused["error"]["code"]
    # Nothing was minted and the original is untouched.
    assert coord.get_workspace("w").tasks[task_id].state == "created"
    assert len(coord.get_workspace("w").tasks) == 1


def test_superseding_to_an_active_participant_still_works():
    coord, send = _ready()
    task_id = send("task.create", kind="draft", input={}, assignee="agent:b")["result"]["task_id"]
    ok = send("control.supersede", task_id=task_id, reason="redo",
              successor_task={"kind": "draft", "input": {}, "assignee": "agent:c"})
    assert "result" in ok
    assert coord.get_workspace("w").tasks[task_id].state == "superseded"
