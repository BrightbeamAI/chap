"""
control.resume restores the state captured at pause, not always in_progress.
A task paused while in review_requested must come back reviewable, otherwise
control.resume (now the only way out of a pause) strands the pending review.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _setup():
    c = Coordinator(CoordinatorOptions(
        deterministic_ids=True,
        default_profiles=["core/1.0", "review/1.0", "control/1.0"]))

    def s(m, actor, **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                           "params": {"workspace": "w", "from": actor, **p}})

    s("workspace.create", "human:a",
      profiles=["core/1.0", "review/1.0", "control/1.0"])
    s("participant.join", "human:a", type="human")
    s("participant.join", "agent:b", type="agent")
    tid = s("task.create", "human:a", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    return c, s, tid


def test_resume_restores_review_requested_and_review_stays_actionable():
    c, s, tid = _setup()
    s("review.request", "agent:b", task_id=tid, artefact={"text": "draft"}, to="human:a")
    assert c.workspaces["w"].tasks[tid].state == "review_requested"
    s("control.pause", "human:a", task_id=tid, reason="hold")
    assert c.workspaces["w"].tasks[tid].state == "paused"

    r = s("control.resume", "human:a", task_id=tid)
    assert r["result"]["state"] == "review_requested"
    assert c.workspaces["w"].tasks[tid].state == "review_requested"

    decided = s("decide.approve", "human:a", task_id=tid, comment="ok", rationale="ok")
    assert "result" in decided
    assert c.workspaces["w"].tasks[tid].state == "completed"


def test_resume_restores_in_progress():
    c, s, tid = _setup()
    s("task.update", "agent:b", task_id=tid, state="in_progress")
    s("control.pause", "human:a", task_id=tid, reason="hold")
    r = s("control.resume", "human:a", task_id=tid)
    assert r["result"]["state"] == "in_progress"


def test_repeated_pause_resumes_in_one_call():
    c, s, tid = _setup()
    s("review.request", "agent:b", task_id=tid, artefact={"text": "draft"}, to="human:a")
    s("control.pause", "human:a", task_id=tid, reason="hold")
    s("control.pause", "human:a", task_id=tid, reason="hold again")
    task = c.workspaces["w"].tasks[tid]
    assert task.paused_from == "review_requested"

    r = s("control.resume", "human:a", task_id=tid)
    assert r["result"]["state"] == "review_requested"
    task = c.workspaces["w"].tasks[tid]
    assert task.state == "review_requested"
    assert task.paused is False


def test_paused_from_is_serialised():
    c, s, tid = _setup()
    s("review.request", "agent:b", task_id=tid, artefact={"text": "draft"}, to="human:a")
    s("control.pause", "human:a", task_id=tid, reason="hold")
    d = c.workspaces["w"].tasks[tid].to_dict()
    assert d["paused_from"] == "review_requested"
