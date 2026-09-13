"""
A task can be reviewed more than once, and the passes are not one review.

The coordinator replaces a review outright when one is requested on a task that
is not currently under review: the new pass starts with no decisions in it.
Holding decisions on the task instead of on the pass pooled them, and then
reported a quorum assembled over two different artefacts as though it had been
assembled over one. Every test here is a case that got a wrong answer.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

from chap_analytics import frames, from_coordinator  # noqa: E402
from chap_analytics.load import Chain  # noqa: E402

PROFILES = ["core/1.0", "review/1.0", "control/1.0"]


def build():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def send(m, p=None, a="human:ana"):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                           "params": {"workspace": "w", "from": a, **(p or {})}})

    send("workspace.create", {"profiles": PROFILES})
    for uri, kind in [("human:ana", "human"), ("human:bo", "human"),
                      ("human:cy", "human"), ("agent:drafter", "agent")]:
        send("participant.join", {"type": kind}, uri)

    def ok(m, p=None, a="human:ana"):
        r = send(m, p, a)
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})
    return c, ok


def both(c):
    """The same workspace read each way, which is where disagreements hide."""
    entries = c.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read",
                          "params": {"workspace": "w", "from": "human:ana"}})["result"]["entries"]
    return (frames(Chain(workspace="w", events=entries, state=None, source="audit.read")),
            frames(from_coordinator(c, workspace="w")))


def submit(ok, rule="quorum:2", to=("human:ana", "human:bo", "human:cy")):
    t = ok("task.create", {"kind": "payout", "input": {"amt": 1},
                           "assignee": "agent:drafter"})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"v": 1}}, "agent:drafter")
    ok("review.request", {"task_id": t, "artefact": {"v": 1},
                          "to": list(to), "rule": rule}, "agent:drafter")
    return t


# --------------------------------------------------- a review requested twice

def test_a_second_review_request_does_not_inherit_the_first_pass_approvals():
    c, ok = build()
    t = submit(ok)
    ok("decide.approve", {"task_id": t}, "human:ana")
    ok("decide.reject", {"task_id": t, "request_revision": True}, "human:bo")
    ok("review.request", {"task_id": t, "artefact": {"v": 2},
                          "to": ["human:ana", "human:bo", "human:cy"],
                          "rule": "quorum:2"}, "agent:drafter")
    ok("decide.approve", {"task_id": t}, "human:cy")

    # One approval in the new pass, and the rule wants two.
    assert c.get_workspace("w").tasks[t].state == "review_requested"
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[t]
        assert row["state"] == "review_requested", f"{label} read"
        assert row["outcome"] == "open", (
            f"{label} read says the work shipped while a reviewer is still owed")
        assert not bool(row["settled"])
        assert row["n_reviews"] == 2
        assert not f.decisions["is_final"].any(), (
            f"{label} read marked a decision as having settled an open review")


def test_settled_at_is_null_on_a_task_the_coordinator_holds_open():
    c, ok = build()
    t = submit(ok)
    ok("decide.approve", {"task_id": t}, "human:ana")
    ok("decide.reject", {"task_id": t, "request_revision": True}, "human:bo")
    ok("review.request", {"task_id": t, "artefact": {"v": 2},
                          "to": ["human:ana"], "rule": "quorum:2"}, "agent:drafter")

    import pandas as pd
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[t]
        assert pd.isna(row["settled_at"]), f"{label}: settled_at set on an open task"
        assert pd.isna(row["lifetime_s"]), f"{label}: a lifetime for work still running"


def test_a_decision_is_never_earlier_than_the_review_it_answers():
    # latency_s was measured from the latest review opening while the decisions
    # kept their own timestamps, so the first pass came out negative.
    c, ok = build()
    t = submit(ok)
    ok("decide.approve", {"task_id": t}, "human:ana")
    ok("decide.reject", {"task_id": t, "request_revision": True}, "human:bo")
    ok("review.request", {"task_id": t, "artefact": {"v": 2},
                          "to": ["human:cy"], "rule": "quorum:2"}, "agent:drafter")
    ok("decide.approve", {"task_id": t}, "human:cy")

    for label, f in zip(("envelopes", "state"), both(c)):
        lat = f.decisions["latency_s"].dropna()
        assert (lat >= 0).all(), f"{label}: {lat.tolist()}"
        assert f.decisions.sort_values("seq")["review_index"].tolist() == [0, 0, 1], (
            f"{label}: decisions must be attributed to the pass they were cast in")


def test_a_review_re_opened_by_completing_again_keeps_its_decisions():
    # The other direction, and the coordinator does pool here: completing a
    # task that requires review reuses the open review rather than replacing
    # it, so the pass carries on with the rejection still in it. Splitting
    # these into two passes would be as wrong as merging the ones above.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:drafter",
                           "review_required": True})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"v": 1}}, "agent:drafter")
    ok("decide.reject", {"task_id": t, "request_revision": True}, "human:ana")
    ok("task.complete", {"task_id": t, "output": {"v": 2}}, "agent:drafter")
    ok("decide.approve", {"task_id": t}, "human:bo")

    task = c.get_workspace("w").tasks[t]
    assert task.state == "completed"
    assert [d["kind"] for d in task.review.decisions] == ["reject", "approve"]
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[t]
        assert row["state"] == "completed", f"{label} read"
        assert row["outcome"] == "approved"
        assert row["n_reviews"] == 1, "completing again reuses the review, it does not open one"
        assert f.decisions.sort_values("seq")["review_index"].tolist() == [0, 0]


# -------------------------------------------- an outcome the chain contradicts

def test_work_that_shipped_after_a_rejection_is_not_reported_as_approved():
    c, ok = build()
    t = ok("task.create", {"kind": "draft", "input": {},
                           "assignee": "agent:drafter"})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"v": 1}}, "agent:drafter")
    ok("review.request", {"task_id": t, "artefact": {"v": 1},
                          "to": ["human:ana"]}, "agent:drafter")
    ok("decide.reject", {"task_id": t, "request_revision": True}, "human:ana")
    ok("task.complete", {"task_id": t, "output": {"v": 2}}, "agent:drafter")

    decisions = c.get_workspace("w").tasks[t].review.decisions
    assert [d["kind"] for d in decisions] == ["reject"], "no approval exists in this chain"
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[t]
        assert row["outcome"] == "completed_after_rejection", (
            f"{label} read reports an approval rate the chain does not support")
        assert bool(row["was_reviewed"]) is True


# ------------------------------------------------- who a rule can wait on

def test_all_approve_with_a_group_addressee_settles_on_the_named_reviewer():
    # A group URI names nobody in particular, so the coordinator cannot wait on
    # it and requires only the reviewers it can name.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:drafter"})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"v": 1}}, "agent:drafter")
    ok("review.request", {"task_id": t, "artefact": {"v": 1},
                          "to": ["human:ana", "group:legal"],
                          "rule": "all_approve"}, "agent:drafter")
    ok("decide.approve", {"task_id": t}, "human:ana")

    assert c.get_workspace("w").tasks[t].state == "completed"
    for label, f in zip(("envelopes", "state"), both(c)):
        assert f.tasks.set_index("task_id").loc[t, "outcome"] == "approved", f"{label} read"
        assert f.decisions["is_final"].tolist() == [True], (
            f"{label}: the approval that completed the task must be marked as final")


def test_all_approve_still_waits_for_the_reviewer_it_can_name():
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:drafter"})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"v": 1}}, "agent:drafter")
    ok("review.request", {"task_id": t, "artefact": {"v": 1},
                          "to": ["human:ana", "group:legal"],
                          "rule": "all_approve"}, "agent:drafter")
    ok("decide.approve", {"task_id": t}, "human:bo")
    ok("decide.approve", {"task_id": t}, "human:cy")

    assert c.get_workspace("w").tasks[t].state == "review_requested"
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[t]
        assert row["outcome"] == "open", (
            f"{label} read shipped work the named reviewer never approved")
        assert not f.decisions["is_final"].any()


def test_a_workspace_default_the_caller_never_sent_still_reaches_the_table():
    # Under modes/1.0 a trial task requires review whether or not the caller
    # asked for it, and the mode itself comes from the workspace. Leaving both
    # to server state left review_required null on an envelope-only read, and
    # a task whose completion opens a review was replayed as one that
    # completes outright: the wrong state, the wrong outcome, the wrong
    # lifetime.
    profiles = ["core/1.0", "review/1.0", "modes/1.0"]
    c = Coordinator(CoordinatorOptions(default_profiles=profiles))

    def ok(m, p=None, a="human:ana"):
        r = c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                        "params": {"workspace": "w", "from": a, **(p or {})}})
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})

    ok("workspace.create", {"profiles": profiles, "mode": "trial"})
    for uri, kind in [("human:ana", "human"), ("human:bo", "human"),
                      ("agent:drafter", "agent")]:
        ok("participant.join", {"type": kind}, uri)
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:drafter"})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"v": 1}}, "agent:drafter")

    assert c.get_workspace("w").tasks[t].state == "review_requested"
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[t]
        assert bool(row["review_required"]) is True, f"{label} read"
        assert row["mode"] == "trial"
        assert row["state"] == "review_requested", (
            f"{label} read let a trial task complete without the review it needs")
        assert row["outcome"] == "open"


def test_a_superseding_successor_is_bound_by_the_same_review_rule_as_a_creation():
    # control.supersede mints a task. The coordinator gives it the original's
    # assignee and mode where the spec names none and applies the review rule
    # task.create applies. Replaying it as a bare creation let a successor
    # whose completion opens a review complete outright, on the envelope-only
    # read, with the row marked certain.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:drafter"})["task_id"]
    s = ok("control.supersede", {"task_id": t, "reason": "Redo.",
                                 "successor_task": {"kind": "v2", "input": {},
                                                    "review_required": True}})["new_task_id"]
    ok("task.complete", {"task_id": s, "output": {"v": 2}}, "agent:drafter")

    successor = c.get_workspace("w").tasks[s]
    assert successor.state == "review_requested"
    assert successor.assignee == "agent:drafter"
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[s]
        assert row["state"] == "review_requested", f"{label} read completed a gated successor"
        assert row["assignee"] == "agent:drafter", f"{label}: the assignee is inherited"
        assert row["mode"] == successor.mode, f"{label}: the mode is inherited"
        assert bool(row["review_required"]) is True
        assert row["outcome"] == "open"


def test_an_escalation_successor_inherits_kind_and_mode():
    c, ok = build()
    t = ok("task.create", {"kind": "payout", "input": {}, "assignee": "agent:drafter",
                           "mode": "shadow"})["task_id"]
    s = ok("escalate.raise", {"original_task_id": t,
                              "new_task": {"assignee": "human:cy", "input": {}}})["new_task_id"]
    ok("task.update", {"task_id": s, "state": "in_progress"}, "human:cy")

    successor = c.get_workspace("w").tasks[s]
    assert (successor.kind, successor.mode) == ("payout", "shadow")
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[s]
        assert row["kind"] == "payout", f"{label}: kind defaults to the original's"
        assert row["mode"] == "shadow", f"{label}: mode is always the original's"


def test_work_that_shipped_while_its_review_was_still_open_is_named_as_such():
    # all_approve to two reviewers, one approves, the assignee takes the task
    # back and completes it. Nothing final was decided, and the state alone
    # would have called it approved.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:drafter"})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"v": 1}}, "agent:drafter")
    ok("review.request", {"task_id": t, "artefact": {"v": 1},
                          "to": ["human:ana", "human:bo"], "rule": "all_approve"}, "agent:drafter")
    ok("decide.approve", {"task_id": t}, "human:ana")
    ok("task.update", {"task_id": t, "state": "in_progress"}, "agent:drafter")
    ok("task.complete", {"task_id": t, "output": {"v": 2}}, "agent:drafter")

    assert c.get_workspace("w").tasks[t].state == "completed"
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[t]
        assert row["outcome"] == "completed_bypassing_review", f"{label} read"
        assert not f.decisions["is_final"].any()


def test_the_assignee_declining_the_work_is_not_a_reviewers_rejection():
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:drafter"})["task_id"]
    ok("task.update", {"task_id": t, "state": "declined"}, "agent:drafter")

    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.tasks.set_index("task_id").loc[t]
        assert row["outcome"] == "declined", f"{label} read"
        assert not bool(row["was_reviewed"]), "no reviewer was ever involved"
        assert bool(row["settled"])


@pytest.mark.parametrize("rule,reviewers,approvers,completes", [
    ("any_one_approves", 3, 1, True),
    ("quorum:2", 3, 1, False),
    ("quorum:2", 3, 2, True),
    ("quorum:3", 3, 2, False),
    ("quorum:3", 3, 3, True),
    ("all_approve", 3, 2, False),
    ("all_approve", 3, 3, True),
])
def test_is_final_agrees_with_the_coordinator_for_every_rule(rule, reviewers, approvers, completes):
    humans = ["human:ana", "human:bo", "human:cy"][:reviewers]
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:drafter"})["task_id"]
    ok("task.complete", {"task_id": t, "output": {"v": 1}}, "agent:drafter")
    ok("review.request", {"task_id": t, "artefact": {"v": 1},
                          "to": humans, "rule": rule}, "agent:drafter")
    for uri in humans[:approvers]:
        ok("decide.approve", {"task_id": t}, uri)

    settled = c.get_workspace("w").tasks[t].state == "completed"
    assert settled is completes, "the fixture no longer exercises what it claims"
    for label, f in zip(("envelopes", "state"), both(c)):
        finals = f.decisions.sort_values("seq")["is_final"].tolist()
        assert finals == [False] * (approvers - 1) + [settled], (
            f"{label} read, {rule} with {approvers} of {reviewers}: {finals}")
