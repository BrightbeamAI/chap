"""
A revised artefact starts a new review round.

A task under review goes back to `in_progress` through `task.update`, or
through a `decide.reject` with `request_revision`. When `task.complete` then
submits a new artefact, the review opens a new round: the reviewers and the
rule stay, and the decisions start empty. Before, the decisions on the earlier
artefact were kept, so under `all_approve` or `quorum:<n>` an approval of the
first artefact counted towards the rule on the second. A decision that names
its artefact with `approved_artefact_digest` cannot be counted again on
another, even when a copy of it is sent again.

Mirrors packages/coordinator/tests/review_rounds.test.ts.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import content_hash
from chap_coordinator.jsonrpc import E

PROFILES = ["core/1.0", "review/1.0"]


def _ready():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES, deterministic_ids=True))

    def send(method, actor="agent:b", **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create", profiles=PROFILES)
    for uri, kind in (("human:a", "human"), ("human:c", "human"), ("agent:b", "agent")):
        send("participant.join", actor=uri, type=kind)
    tid = send("task.create", kind="k", input={}, assignee="agent:b",
               review_required=True)["result"]["task_id"]
    send("task.update", task_id=tid, state="in_progress")
    return c, send, tid


def _task(c, tid):
    return c.get_workspace("w").tasks[tid]


def _ok(reply):
    assert "error" not in reply, reply.get("error")
    return reply["result"]


@pytest.mark.parametrize("rule", ["all_approve", "quorum:2"])
def test_an_approval_of_the_first_artefact_does_not_count_for_the_second(rule):
    c, send, tid = _ready()
    _ok(send("review.request", task_id=tid, to=["human:a", "human:c"], rule=rule,
             artefact={"draft": "v1"}))
    _ok(send("decide.approve", actor="human:a", task_id=tid))
    assert _task(c, tid).state == "review_requested"

    _ok(send("task.update", task_id=tid, state="in_progress"))
    assert _ok(send("task.complete", task_id=tid, output={"draft": "v2"}))["state"] == "review_requested"
    review = _task(c, tid).review
    assert review.decisions == []
    assert review.requested_to == ["human:a", "human:c"]
    assert review.rule == rule

    _ok(send("decide.approve", actor="human:c", task_id=tid))
    assert _task(c, tid).state == "review_requested", \
        "an approval of the first artefact completed the second"
    assert _task(c, tid).output is None

    _ok(send("decide.approve", actor="human:a", task_id=tid))
    assert _task(c, tid).state == "completed"
    assert _task(c, tid).output == {"draft": "v2"}


def test_after_a_reject_with_request_revision_the_new_artefact_starts_a_new_round():
    c, send, tid = _ready()
    _ok(send("review.request", task_id=tid, to=["human:a", "human:c"], rule="quorum:2",
             artefact={"draft": "v1"}))
    _ok(send("decide.approve", actor="human:a", task_id=tid))
    _ok(send("decide.reject", actor="human:c", task_id=tid, comment="redo",
             request_revision=True))
    assert _task(c, tid).state == "in_progress"

    _ok(send("task.complete", task_id=tid, output={"draft": "v2"}))
    assert _task(c, tid).review.decisions == []
    _ok(send("decide.approve", actor="human:c", task_id=tid))
    assert _task(c, tid).state == "review_requested"
    _ok(send("decide.approve", actor="human:a", task_id=tid))
    assert _task(c, tid).state == "completed"
    assert _task(c, tid).output == {"draft": "v2"}


def test_an_implicit_review_keeps_its_reviewers_in_the_new_round():
    c, send, tid = _ready()
    _ok(send("task.complete", task_id=tid, output={"draft": "v1"}))
    assert _task(c, tid).review.requested_to == ["human:a", "human:c"]
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))

    _ok(send("task.complete", task_id=tid, output={"draft": "v2"}))
    second = _task(c, tid).review
    assert second.requested_to == ["human:a", "human:c"]
    assert second.rule == "any_one_approves"
    assert second.decisions == []
    _ok(send("decide.approve", actor="human:c", task_id=tid))
    assert _task(c, tid).output == {"draft": "v2"}


def test_a_reviewer_who_submits_the_new_artefact_is_not_among_its_reviewers():
    c, send, tid = _ready()
    _ok(send("review.request", task_id=tid, to=["human:a", "human:c"], rule="any_one_approves",
             artefact={"draft": "v1"}))
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    # Any member may complete the task, so a reviewer can submit the revision.
    _ok(send("task.complete", actor="human:a", task_id=tid, output={"draft": "v2 by a"}))
    assert _task(c, tid).review.requested_to == ["human:c"]
    assert "error" in send("decide.approve", actor="human:a", task_id=tid), \
        "the author approved its own artefact"
    assert _task(c, tid).state == "review_requested"
    _ok(send("decide.approve", actor="human:c", task_id=tid))
    assert _task(c, tid).state == "completed"


def test_a_completion_that_would_leave_the_review_with_no_reviewer_is_refused():
    c, send, tid = _ready()
    _ok(send("review.request", task_id=tid, to=["human:a"], artefact={"draft": "v1"}))
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    r = send("task.complete", actor="human:a", task_id=tid, output={"draft": "v2"})
    assert r["error"]["code"] == -32011
    assert _task(c, tid).state == "in_progress"
    assert _task(c, tid).review.requested_to == ["human:a"]


def test_the_first_rounds_decisions_stay_on_the_chain():
    c, send, tid = _ready()
    _ok(send("review.request", task_id=tid, to=["human:a", "human:c"], rule="all_approve",
             artefact={"draft": "v1"}))
    _ok(send("decide.approve", actor="human:a", task_id=tid))
    _ok(send("task.update", task_id=tid, state="in_progress"))
    _ok(send("task.complete", task_id=tid, output={"draft": "v2"}))
    entries = _ok(send("audit.read", actor="human:a", filter={"task_id": tid}))["entries"]
    approvals = [e for e in entries if (e.get("envelope") or {}).get("method") == "decide.approve"]
    assert len(approvals) == 1


def test_a_copy_of_a_decision_that_names_the_first_artefact_is_refused_in_the_new_round():
    c, send, tid = _ready()
    _ok(send("review.request", task_id=tid, to=["human:a", "human:c"], rule="all_approve",
             artefact={"draft": "v1"}))
    approval = {"task_id": tid, "approved_artefact_digest": content_hash({"draft": "v1"})}
    _ok(send("decide.approve", actor="human:a", **approval))
    _ok(send("task.update", task_id=tid, state="in_progress"))
    _ok(send("task.complete", task_id=tid, output={"draft": "v2"}))

    copy = send("decide.approve", actor="human:a", **approval)
    assert copy["error"]["code"] == E.SIG_ARTEFACT_DIGEST_MISMATCH
    assert _task(c, tid).review.decisions == []
    assert _task(c, tid).state == "review_requested"
