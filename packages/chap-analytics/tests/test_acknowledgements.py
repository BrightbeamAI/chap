"""
review.acknowledge: a reviewer has opened a review. Where the log records
it, the wait for a decision splits into the time before a reviewer opened
the review and the time they took with it in front of them.

The log is written out here as a coordinator from 0.3.5 records it: the
acknowledgement is an accepted call, {"method": "review.acknowledge",
"params": {"workspace", "from", "task_id"}}, once per reviewer per pass.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")

import pandas as pd  # noqa: E402

from chap_analytics import Chain, briefs, frames, stats  # noqa: E402

T0 = pd.Timestamp("2026-11-02T09:00:00Z")


class Log:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.call("workspace.create", "human:ana", 0)
        for uri, kind in (("human:ana", "human"), ("human:ben", "human"), ("agent:drafter", "agent")):
            self.call("participant.join", uri, 0, type=kind)

    def call(self, method: str, actor: str, minutes: float, **params) -> None:
        ts = (T0 + pd.Timedelta(minutes=minutes)).isoformat()
        self.events.append({
            "seq": len(self.events), "arrived": ts, "prev_hash": "sha256:" + "0" * 64,
            "envelope": {"jsonrpc": "2.0", "id": str(len(self.events)), "method": method,
                         "params": {"workspace": "wsp", "from": actor, "ts": ts, **params}}})

    def task(self, task_id: str, minutes: float) -> None:
        """A task created and sent for review to Ana and Ben."""
        self.call("task.create", "agent:drafter", minutes, kind="reply",
                  input={"n": task_id}, assignee="agent:drafter")
        self.call("review.request", "agent:drafter", minutes, task_id=task_id,
                  artefact={"body": task_id}, to=["human:ana", "human:ben"])

    def frames(self):
        return frames(Chain(workspace="wsp", events=self.events, state=None, source="test"))


def test_a_decision_carries_when_its_reviewer_opened_the_review():
    log = Log()
    log.task("tsk_1", 0)
    log.call("review.acknowledge", "human:ana", 30, task_id="tsk_1")
    log.call("review.acknowledge", "human:ana", 32, task_id="tsk_1")  # the first counts
    log.call("decide.approve", "human:ana", 35, task_id="tsk_1")
    log.task("tsk_2", 40)
    log.call("decide.approve", "human:ben", 50, task_id="tsk_2")
    d = log.frames().decisions.set_index("task_id")

    assert d.loc["tsk_1", "acknowledged_at"] == T0 + pd.Timedelta(minutes=30)
    assert d.loc["tsk_1", "time_to_open_s"] == 30 * 60
    assert d.loc["tsk_1", "time_to_decide_s"] == 5 * 60
    assert d.loc["tsk_1", "latency_s"] == 35 * 60
    assert pd.isna(d.loc["tsk_2", "acknowledged_at"])
    assert pd.isna(d.loc["tsk_2", "time_to_open_s"])


def test_an_acknowledgement_belongs_to_its_own_pass():
    """Ana opened the first pass; the revision opens a second, which she
    never opened before deciding."""
    log = Log()
    log.task("tsk_1", 0)
    log.call("review.acknowledge", "human:ana", 10, task_id="tsk_1")
    log.call("decide.reject", "human:ana", 12, task_id="tsk_1", request_revision=True,
             comment="shorter")
    log.call("review.request", "agent:drafter", 20, task_id="tsk_1",
             artefact={"body": "tsk_1, shorter"}, to=["human:ana"])
    log.call("decide.approve", "human:ana", 25, task_id="tsk_1")
    f = log.frames()
    d = f.decisions.sort_values("seq")
    assert list(d["time_to_open_s"].fillna(-1)) == [10 * 60, -1]
    passes = stats.latency(f).sort_values("review_index")
    assert list(passes["time_to_open_s"].fillna(-1)) == [10 * 60, -1]


def test_latency_splits_each_pass_at_its_first_opening():
    log = Log()
    log.task("tsk_1", 0)
    log.call("review.acknowledge", "human:ben", 20, task_id="tsk_1")
    log.call("review.acknowledge", "human:ana", 30, task_id="tsk_1")
    log.call("decide.approve", "human:ana", 35, task_id="tsk_1")
    log.task("tsk_2", 40)                                      # open, opened
    log.call("review.acknowledge", "human:ana", 45, task_id="tsk_2")
    log.task("tsk_3", 50)                                      # open, unopened
    log.call("task.create", "agent:drafter", 60, kind="other", input={}, assignee="agent:drafter")
    lt = stats.latency(log.frames()).set_index("task_id")

    assert lt.loc["tsk_1", "acknowledged_at"] == T0 + pd.Timedelta(minutes=20)
    assert (lt.loc["tsk_1", "time_to_open_s"], lt.loc["tsk_1", "time_to_decide_s"]) == (20 * 60, 15 * 60)
    assert lt.loc["tsk_2", "time_to_open_s"] == 5 * 60 and pd.isna(lt.loc["tsk_2", "time_to_decide_s"])
    assert pd.isna(lt.loc["tsk_3", "acknowledged_at"])
    q = stats.open_queue(log.frames()).set_index("task_id")
    assert pd.notna(q.loc["tsk_2", "acknowledged_at"]) and pd.isna(q.loc["tsk_3", "acknowledged_at"])


def test_the_brief_reports_time_to_open_and_time_to_decide():
    log = Log()
    for i in range(6):
        start = 60 * i
        log.task(f"tsk_{i}", start)
        log.call("review.acknowledge", "human:ana", start + 40, task_id=f"tsk_{i}")
        log.call("decide.approve", "human:ana", start + 42, task_id=f"tsk_{i}")
    log.task("tsk_open", 400)
    b = briefs.latency(log.frames())
    assert ("half were opened within 40 minutes of the request, and half decided within "
            "2 minutes of being opened") in b.text
    assert "Of the 1 still waiting, 1 has not been opened." in b.text
    assert b.decision.startswith("Most of the wait comes before a reviewer opens the work")
    assert b.numbers["median_open_s"] == 40 * 60 and b.numbers["median_decide_s"] == 2 * 60


def test_a_log_without_acknowledgements_reads_as_before():
    log = Log()
    log.task("tsk_1", 0)
    log.call("decide.approve", "human:ana", 35, task_id="tsk_1")
    log.task("tsk_2", 40)
    f = log.frames()
    b = briefs.latency(f)
    assert "acknowledged" not in b.text and "not been opened" not in b.text
    assert f.decisions["acknowledged_at"].isna().all()
