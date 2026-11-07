"""
A reading a 0.3.5 log needs, on chains the coordinator writes.

A completion on a task that has held a review opens a new pass from
chap-coordinator 0.3.5, and completed the task before it. The replay holds
such a pass until something shows which coordinator wrote the log. An
acknowledgement shows it as a decision does, since a coordinator takes one
only while a review is open: it belongs to the pass that completion opened,
whose times to open and to decide count from there, and a log read without
state shows the acknowledged review as open.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

import pandas as pd  # noqa: E402

from chap_analytics import Chain, frames, from_coordinator, stats  # noqa: E402
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

    def revised_then_acknowledged(self) -> str:
        """A review requested on a task that does not require one, sent back,
        completed again, which opens a new pass, then acknowledged."""
        tid = self("task.create", AGENT, kind="reply", input={}, assignee=AGENT)["task_id"]
        self("task.complete", AGENT, task_id=tid, output={"body": "v1"})
        self("review.request", AGENT, task_id=tid, artefact={"body": "v1"}, to=ALICE)
        self("decide.reject", ALICE, task_id=tid, comment="shorter", request_revision=True)
        assert self("task.complete", AGENT, task_id=tid, output={"body": "v2"})["state"] == "review_requested"
        self("review.acknowledge", ALICE, task_id=tid)
        return tid


def test_an_acknowledgement_after_a_completion_that_opened_a_pass_belongs_to_that_pass():
    desk = Desk()
    tid = desk.revised_then_acknowledged()
    desk("decide.approve", ALICE, task_id=tid)
    d = frames(from_coordinator(desk.coord, WS)).decisions.sort_values("review_index")
    assert list(d["kind"]) == ["reject", "approve"]
    assert pd.isna(d.iloc[0]["acknowledged_at"])
    assert d.iloc[1]["acknowledged_at"] < d.iloc[1]["ts"]
    assert d.iloc[1]["time_to_open_s"] > 0 and d.iloc[1]["time_to_decide_s"] > 0


def test_an_acknowledged_pass_reads_as_open_from_the_log_alone():
    desk = Desk()
    desk.revised_then_acknowledged()
    chain = from_coordinator(desk.coord, WS)
    for reading in (chain, Chain(workspace=WS, events=chain.events, state=None, source="export")):
        f = frames(reading)
        assert list(f.tasks["outcome"]) == ["open"]
        assert len(stats.open_queue(f)) == 1
        assert stats.open_queue(f)["acknowledged_at"].notna().all()


def test_an_acknowledgement_outside_an_open_pass_is_left_out():
    """A hand-written log can hold one; a coordinator takes none."""
    desk = Desk()
    tid = desk("task.create", AGENT, kind="reply", input={}, assignee=AGENT)["task_id"]
    desk("task.complete", AGENT, task_id=tid, output={"body": "v1"})
    desk("review.request", AGENT, task_id=tid, artefact={"body": "v1"}, to=ALICE)
    desk("decide.approve", ALICE, task_id=tid)
    events = list(from_coordinator(desk.coord, WS).events)
    late = dict(events[-1], seq=len(events))
    late["envelope"] = {**late["envelope"], "method": "review.acknowledge",
                        "params": {"workspace": WS, "from": ALICE, "task_id": tid}}
    d = frames(Chain(workspace=WS, events=events + [late], state=None, source="export")).decisions
    assert pd.isna(d.iloc[0]["acknowledged_at"])
