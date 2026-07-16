"""
Regression: task.complete is only legal from an active state (created or
in_progress). It must not revive a terminated task (cancelled/superseded) or
bypass a pause. Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _mk():
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "review/1.0", "control/1.0"]))

    def s(m, **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m, "params": p})

    s("workspace.create", workspace="w", profiles=["core/1.0", "review/1.0", "control/1.0"])
    s("participant.join", workspace="w", **{"from": "agent:bot"}, type="agent")
    return c, s


def _new_task(s):
    return s("task.create", workspace="w", **{"from": "agent:bot"}, kind="x",
             input={}, assignee="agent:bot")["result"]["task_id"]


def test_cannot_complete_cancelled_task():
    c, s = _mk()
    tid = _new_task(s)
    s("control.cancel", workspace="w", **{"from": "agent:bot"}, task_id=tid, reason="stop")
    r = s("task.complete", workspace="w", **{"from": "agent:bot"}, task_id=tid, artefact={"body": "x"})
    assert "error" in r


def test_cannot_complete_paused_task():
    c, s = _mk()
    tid = _new_task(s)
    s("control.pause", workspace="w", **{"from": "agent:bot"}, scope="task", task_id=tid)
    r = s("task.complete", workspace="w", **{"from": "agent:bot"}, task_id=tid, artefact={"body": "x"})
    assert "error" in r


def test_can_complete_active_task():
    c, s = _mk()
    tid = _new_task(s)
    assert "result" in s("task.complete", workspace="w", **{"from": "agent:bot"}, task_id=tid, artefact={"body": "x"})


def test_cannot_double_complete():
    c, s = _mk()
    tid = _new_task(s)
    s("task.complete", workspace="w", **{"from": "agent:bot"}, task_id=tid, artefact={"body": "1"})
    assert "error" in s("task.complete", workspace="w", **{"from": "agent:bot"}, task_id=tid, artefact={"body": "2"})
