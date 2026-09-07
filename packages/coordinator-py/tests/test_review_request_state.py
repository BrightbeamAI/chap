"""
Regression: review.request only opens a review on live work.

task.complete refuses a task that has been stopped so that a completion "can
neither revive a terminated task nor bypass a pause". review.request carried no
such check, so a request revived a cancelled or superseded task and pulled a
paused one back into play.

Refused from: cancelled, superseded, paused, with `-32010`. `completed` stays
legal: completing and then requesting review is how the framework bridges
submit a draft.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E

PROFILES = ["core/1.0", "review/1.0", "control/1.0"]
ARTEFACT = {"text": "draft"}


def _ready():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES, deterministic_ids=True))

    def s(method, actor="agent:b", **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    s("workspace.create", profiles=PROFILES)
    for uri, kind in (("human:a", "human"), ("human:c", "human"), ("agent:b", "agent")):
        s("participant.join", actor=uri, type=kind)
    return c, s


def _task(s, state):
    """A task sitting in `state`."""
    tid = s("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    if state == "created":
        pass
    elif state == "in_progress":
        s("task.update", task_id=tid, state="in_progress")
    elif state == "review_requested":
        s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
    elif state == "completed":
        s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
        s("decide.approve", actor="human:a", task_id=tid, comment="ok", rationale="ok")
    elif state == "declined":
        s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
        s("decide.reject", actor="human:a", task_id=tid, comment="no", rationale="no")
    elif state == "abstained":
        s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
        s("abstain.declare", actor="human:a", task_id=tid, reason="conflict of interest")
    elif state == "escalated":
        s("escalate.raise", actor="human:a", original_task_id=tid,
          new_task={"kind": "k", "input": {}, "assignee": "agent:b"}, reason="above me")
    elif state == "paused":
        s("control.pause", actor="human:a", task_id=tid, reason="hold")
    elif state == "cancelled":
        s("control.cancel", actor="human:a", task_id=tid, reason="not needed")
    elif state == "superseded":
        s("control.supersede", actor="human:a", task_id=tid, reason="redone",
          successor_task={"kind": "k", "input": {}, "assignee": "agent:b"})
    else:
        raise AssertionError(state)
    return tid


def _state(c, tid):
    return c.get_workspace("w").tasks[tid].state


@pytest.mark.parametrize("state", ["created", "in_progress", "completed",
                                   "declined", "abstained", "escalated"])
def test_a_review_still_opens_on_work_that_has_not_been_stopped(state):
    c, s = _ready()
    tid = _task(s, state)
    r = s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
    assert "error" not in r, r
    assert _state(c, tid) == "review_requested"


def test_the_bridge_pattern_still_works():
    # Every published framework bridge does task.complete then review.request.
    # If this ever fails, five packages are broken.
    c, s = _ready()
    tid = s("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    s("task.complete", task_id=tid, output=ARTEFACT)
    assert _state(c, tid) == "completed"
    r = s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
    assert "error" not in r, r
    assert _state(c, tid) == "review_requested"


def test_an_open_review_can_still_be_widened():
    # review_requested stays legal, which is what makes the amend path work.
    c, s = _ready()
    tid = _task(s, "review_requested")
    r = s("review.request", task_id=tid, artefact=ARTEFACT, to="human:c")
    assert r["result"]["amended"] is True
    assert set(c.get_workspace("w").tasks[tid].review.requested_to) == {"human:a", "human:c"}


@pytest.mark.parametrize("state", ["cancelled", "superseded", "paused"])
def test_a_stopped_task_cannot_be_pulled_back_into_review(state):
    c, s = _ready()
    tid = _task(s, state)
    assert _state(c, tid) == state, "the fixture did not reach the state under test"

    r = s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")

    assert "error" in r, f"review.request re-opened a {state} task"
    assert r["error"]["code"] == E.NOT_REVIEWABLE
    assert state in r["error"]["message"]
    assert _state(c, tid) == state, "the refused request still moved the task"


@pytest.mark.parametrize("state", ["cancelled", "superseded", "paused"])
def test_the_refusal_records_nothing(state):
    # A refused call must not append. A stopped task that "grew" an entry would
    # leave an audit chain describing a review that never opened.
    c, s = _ready()
    tid = _task(s, state)
    before = len(s("audit.read", actor="human:a")["result"]["entries"])
    s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
    after = len(s("audit.read", actor="human:a")["result"]["entries"])
    assert after == before


def test_a_paused_task_resumes_before_it_can_be_reviewed():
    # The pause is not a dead end; it just has to be lifted deliberately.
    c, s = _ready()
    tid = _task(s, "paused")
    assert "error" in s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
    s("control.resume", actor="human:a", task_id=tid, reason="carry on")
    assert _state(c, tid) == "in_progress"
    r = s("review.request", task_id=tid, artefact=ARTEFACT, to="human:a")
    assert "error" not in r, r
    assert _state(c, tid) == "review_requested"
