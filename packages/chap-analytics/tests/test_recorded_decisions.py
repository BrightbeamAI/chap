"""
Decisions a framework bridge recorded, on chains the coordinator and the
bridge write.

A framework bridge records a decision made in an interface outside CHAP in
a person's name, marked via "bridge-record". Its time on the log measures
no review, and the latency brief read it as instant human review; a
decision unmarked and as quick went unremarked, and a long tail of quick
decisions with nothing waiting was taken for stuck reviews to chase.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

import pandas as pd  # noqa: E402

from chap_analytics import Chain, briefs, frames, from_coordinator, stats  # noqa: E402
from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

WS = "wsp"
ALICE = "human:alice"
AGENT = "agent:drafter"


class Desk:
    """A workspace driven through the coordinator, one second per call."""

    def __init__(self) -> None:
        self.coord = Coordinator(CoordinatorOptions(enable_chain=True, deterministic_clock=True))
        self("workspace.create", None, profiles=["core/1.0", "review/1.0"])
        self("participant.join", AGENT, type="agent")
        self("participant.join", ALICE, type="human")

    def __call__(self, method, sender, **params):
        out = self.coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                                   "params": {"workspace": WS, **({"from": sender} if sender else {}), **params}})
        assert "error" not in out, out
        return out["result"]


def recorded_chain(n: int):
    """A chain on which a framework bridge recorded ``n`` decisions made
    outside CHAP."""
    from chap_coordinator.bridge import ReviewClient

    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ReviewClient(coord, WS, AGENT, ALICE)
    for i in range(n):
        bridge.record({"amount": i}, "approve", task_kind="transfer")
    return coord


def decided_after(*seconds: float) -> Chain:
    """Reviews Alice decided at a desk, each the given number of seconds
    after its request, read from the log alone."""
    desk = Desk()
    for i in range(len(seconds)):
        tid = desk("task.create", AGENT, kind="reply", input={}, assignee=AGENT)["task_id"]
        desk("task.complete", AGENT, task_id=tid, output={"body": i})
        desk("review.request", AGENT, task_id=tid, artefact={"body": i}, to=ALICE)
        desk("decide.approve", ALICE, task_id=tid)
    events, waits = [], iter(seconds)
    for e in from_coordinator(desk.coord, WS).events:
        if e["envelope"]["method"] == "decide.approve":
            requested = pd.Timestamp(events[-1]["arrived"])
            e = dict(e, arrived=(requested + pd.Timedelta(seconds=next(waits))).isoformat())
        events.append(e)
    return Chain(workspace=WS, events=events, state=None, source="export")


def test_a_recorded_decision_says_how_it_reached_the_log():
    f = frames(from_coordinator(recorded_chain(3), WS))
    assert list(f.decisions["via"]) == ["bridge-record"] * 3
    assert list(stats.latency(f)["via"]) == ["bridge-record"] * 3


def test_the_latency_brief_leaves_recorded_decisions_out_of_its_times():
    f = frames(from_coordinator(recorded_chain(12), WS))
    b = briefs.latency(f)
    assert b.headline == "Every decision here was made outside CHAP, so none times a review."
    assert "12 review passes were settled by decisions made outside CHAP" in b.text
    assert b.decision == "Nothing waiting."
    assert stats.latency_by(f).empty and stats.survival(f).empty


def test_the_latency_brief_points_out_decisions_too_soon_to_have_been_read():
    """Decisions in the instant of their request, with nothing to say how:
    a bridge from before 0.3.5 records them unmarked."""
    b = briefs.latency(frames(decided_after(0, 0, 0)))
    assert "3 decisions came within a second of their request, too soon for anyone to have read the work" in b.text


def test_a_long_tail_with_nothing_waiting_is_nothing_to_chase():
    b = briefs.latency(frames(decided_after(*([60] * 9), 6000)))
    assert b.decision == "Decisions are keeping up with the work."
