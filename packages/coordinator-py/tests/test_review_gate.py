"""
Two ways round the review gate, both closed.

`task.complete` opens a review rather than completing when a task requires one.
`task.update` reaches `completed` by another route and carried no such check, so
a required review could be skipped entirely: the task finished, no artefact was
recorded, and no `decide.*` appeared on the chain.

Separately, an open review may be widened with the same artefact. The rule was
compared after the default had been applied, so omitting `rule` on the second
request counted as changing it and the documented widening path was refused.

Mirrored by packages/coordinator/tests/review_gate.test.ts.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E

PROFILES = ["core/1.0", "review/1.0"]
ARTEFACT = {"draft": "text"}


def _ready():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES, deterministic_ids=True))

    def send(method, actor="agent:b", **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create", profiles=PROFILES)
    for uri, kind in (("human:a", "human"), ("human:c", "human"), ("agent:b", "agent")):
        send("participant.join", actor=uri, type=kind)
    return c, send


def _task(c, tid):
    return c.get_workspace("w").tasks[tid]


def _required_task(send, state="in_progress"):
    tid = send("task.create", kind="k", input={}, assignee="agent:b",
               review_required=True)["result"]["task_id"]
    if state == "in_progress":
        send("task.update", task_id=tid, state="in_progress")
    return tid


# -- task.update must not finish work that needs reviewing -----------------

def test_task_update_cannot_complete_a_task_that_requires_review():
    c, send = _ready()
    tid = _required_task(send)
    before = len(send("audit.read", actor="human:a")["result"]["entries"])

    r = send("task.update", task_id=tid, state="completed")

    assert "error" in r, "a required review was skipped by task.update"
    assert r["error"]["code"] == E.PARAMS
    assert "task.complete" in r["error"]["message"], "the message should name the way through"
    task = _task(c, tid)
    assert task.state == "in_progress"
    assert task.output is None
    assert task.review is None
    assert len(send("audit.read", actor="human:a")["result"]["entries"]) == before


def test_from_created_the_transition_is_illegal_before_the_gate_is_reached():
    # Two refusals for the same call. The lifecycle table rejects
    # created -> completed on its own, so the gate is defence in depth.
    c, send = _ready()
    tid = _required_task(send, "created")
    r = send("task.update", task_id=tid, state="completed")
    assert r["error"]["code"] == E.PARAMS
    assert _task(c, tid).state == "created"


def test_the_route_through_is_complete_then_decide():
    c, send = _ready()
    tid = _required_task(send)
    assert send("task.complete", task_id=tid, output=ARTEFACT)["result"]["state"] == "review_requested"
    assert send("decide.approve", actor="human:a", task_id=tid,
                comment="ok", rationale="ok")["result"]["state"] == "completed"
    assert _task(c, tid).output == ARTEFACT


def test_task_update_still_completes_a_task_that_needs_no_review():
    c, send = _ready()
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    send("task.update", task_id=tid, state="in_progress")
    assert "error" not in send("task.update", task_id=tid, state="completed")
    assert _task(c, tid).state == "completed"


@pytest.mark.parametrize("state", ["in_progress", "declined", "paused"])
def test_the_other_task_update_transitions_are_unaffected(state):
    c, send = _ready()
    tid = _required_task(send, "created")
    assert "error" not in send("task.update", task_id=tid, state=state), state
    assert _task(c, tid).state == state


# -- widening an open review -----------------------------------------------

def test_a_reviewer_can_be_added_without_restating_the_rule():
    c, send = _ready()
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    send("review.request", task_id=tid, artefact=ARTEFACT, to=["human:a"], rule="all_approve")

    r = send("review.request", task_id=tid, artefact=ARTEFACT, to=["human:c"])

    assert "error" not in r, r
    assert r["result"]["amended"] is True
    assert set(_task(c, tid).review.requested_to) == {"human:a", "human:c"}
    assert _task(c, tid).review.rule == "all_approve", "the rule must not have moved"


def test_restating_the_same_rule_is_still_accepted():
    c, send = _ready()
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    send("review.request", task_id=tid, artefact=ARTEFACT, to=["human:a"], rule="all_approve")
    r = send("review.request", task_id=tid, artefact=ARTEFACT, to=["human:c"], rule="all_approve")
    assert r["result"]["amended"] is True


def test_a_different_rule_is_still_refused():
    c, send = _ready()
    tid = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    send("review.request", task_id=tid, artefact=ARTEFACT, to=["human:a"], rule="all_approve")

    r = send("review.request", task_id=tid, artefact=ARTEFACT, to=["human:c"],
             rule="any_one_approves")

    assert r["error"]["code"] == E.REVIEW_ALREADY_OPEN
    assert _task(c, tid).review.rule == "all_approve"
    assert _task(c, tid).review.requested_to == ["human:a"], "the refusal changed nothing"
