"""
Regression: whisper.answer may only be answered by a participant the whisper
was addressed to (its askee set); a broadcast scope is satisfied by any
member. Guards the 0.2.7 fix against an arbitrary party answering a directed
whisper.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _setup():
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "whisper/1.0"]))

    def s(m, **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m, "params": p})

    s("workspace.create", workspace="w", profiles=["core/1.0", "whisper/1.0"])
    for u, t in [("agent:bot", "agent"), ("human:alice", "human"), ("human:mallory", "human")]:
        s("participant.join", workspace="w", **{"from": u}, type=t)
    tid = s("task.create", workspace="w", **{"from": "agent:bot"}, kind="x", input={}, assignee="agent:bot")["result"]["task_id"]
    wid = s("whisper.ask", workspace="w", **{"from": "agent:bot"}, to="human:alice", task_id=tid,
            question="?", deadline_ms=60000, default_if_lapsed="no", options=[{"id": "yes"}])["result"]["whisper_id"]
    return c, s, wid


def test_non_askee_cannot_answer():
    c, s, wid = _setup()
    r = s("whisper.answer", workspace="w", **{"from": "human:mallory"}, whisper_id=wid, answer_option="yes")
    assert "error" in r and r["error"]["code"] == -32011


def test_addressed_askee_can_answer():
    c, s, wid = _setup()
    r = s("whisper.answer", workspace="w", **{"from": "human:alice"}, whisper_id=wid, answer_option="yes")
    assert "result" in r
