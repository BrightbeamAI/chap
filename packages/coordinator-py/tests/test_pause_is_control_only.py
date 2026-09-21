"""
Regression (#157): task.update reaches no paused state.

#142 removed paused -> in_progress from task.update, leaving control.resume as
the only way out of a pause while pause itself stayed reachable from Core. A
workspace advertising core/1.0 alone could then hold a task it had no
advertised method to lift.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions

CORE_ONLY = ["core/1.0", "review/1.0"]
WITH_CONTROL = [*CORE_ONLY, "control/1.0"]


def _ready(profiles):
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, default_profiles=profiles))

    def send(method, sender="human:a", **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                               "params": {"workspace": "w", "from": sender, **params}})

    send("workspace.create", profiles=profiles)
    for uri, kind in (("human:a", "human"), ("agent:b", "agent")):
        send("participant.join", sender=uri, type=kind)
    return coord, send


@pytest.mark.parametrize("state", ["created", "in_progress"])
def test_task_update_cannot_pause_a_task(state):
    coord, send = _ready(CORE_ONLY)
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    if state == "in_progress":
        send("task.update", task_id=tid, state="in_progress", sender="agent:b")

    refused = send("task.update", task_id=tid, state="paused", sender="agent:b")

    assert "error" in refused
    assert refused["error"]["message"] == f"Illegal transition {state} -> paused"
    assert coord.get_workspace("w").tasks[tid].state == state


def test_control_pause_and_resume_are_the_pair():
    coord, send = _ready(WITH_CONTROL)
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    send("task.update", task_id=tid, state="in_progress", sender="agent:b")

    assert "error" not in send("control.pause", task_id=tid, reason="hold")
    assert coord.get_workspace("w").tasks[tid].state == "paused"
    assert "error" not in send("control.resume", task_id=tid)
    assert coord.get_workspace("w").tasks[tid].state == "in_progress"


def test_a_paused_task_can_still_be_cancelled_through_task_update():
    coord, send = _ready(WITH_CONTROL)
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    send("control.pause", task_id=tid, reason="hold")
    assert "error" not in send("task.update", task_id=tid, state="cancelled")
    assert coord.get_workspace("w").tasks[tid].state == "cancelled"
