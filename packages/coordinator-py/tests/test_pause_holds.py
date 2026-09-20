"""
Regression: a task-scoped control.pause must actually stop updates. A paused
task cannot be walked back to in_progress with task.update; control.resume is
the only way out.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _paused_task():
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "control/1.0"]))

    def s(m, actor, **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                           "params": {"workspace": "w", "from": actor, **p}})

    s("workspace.create", "human:gov", profiles=["core/1.0", "control/1.0"])
    s("participant.join", "human:gov", type="human")
    s("participant.join", "agent:worker", type="agent")
    tid = s("task.create", "human:gov", kind="k", input={}, assignee="agent:worker")["result"]["task_id"]
    s("task.update", "agent:worker", task_id=tid, state="in_progress")
    s("control.pause", "human:gov", task_id=tid, reason="hold")
    assert c.workspaces["w"].tasks[tid].state == "paused"
    return c, s, tid


def test_paused_task_cannot_be_resumed_with_task_update():
    c, s, tid = _paused_task()
    r = s("task.update", "agent:worker", task_id=tid, state="in_progress")
    assert "error" in r and r["error"]["code"] == -32602
    assert c.workspaces["w"].tasks[tid].state == "paused"


def test_control_resume_is_the_only_way_out():
    c, s, tid = _paused_task()
    r = s("control.resume", "human:gov", task_id=tid)
    assert "result" in r
    assert c.workspaces["w"].tasks[tid].state == "in_progress"


def test_paused_task_may_still_be_cancelled():
    c, s, tid = _paused_task()
    r = s("task.update", "agent:worker", task_id=tid, state="cancelled")
    assert "result" in r
    assert c.workspaces["w"].tasks[tid].state == "cancelled"
