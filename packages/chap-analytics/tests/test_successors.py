"""
A successor keeps the review requirement of the task it replaces.

From 0.3.0 the coordinators give the successor that escalate.raise or
control.supersede makes the review requirement of the task it replaces, and
require review of a trial successor under modes/1.0. The replay follows, or
completing such a successor would read as finished where the coordinator
opened a review.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

import pandas as pd  # noqa: E402
from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

from chap_analytics import frames, from_coordinator  # noqa: E402
from chap_analytics.load import Chain  # noqa: E402

PROFILES = ["core/1.0", "review/1.0", "control/1.0"]


def build():
    c = Coordinator(CoordinatorOptions())
    send = lambda m, p=None, a="human:ana": c.dispatch({  # noqa: E731
        "jsonrpc": "2.0", "id": m, "method": m,
        "params": {"workspace": "w", "from": a, **(p or {})}})
    send("workspace.create", {"profiles": PROFILES})
    for uri, kind, role in [("human:ana", "human", "admin"), ("human:bo", "human", "reviewer"),
                            ("agent:x", "agent", "drafter"), ("agent:y", "agent", "drafter")]:
        send("participant.join", {"type": kind, "role": role}, uri)

    def ok(m, p=None, a="human:ana"):
        r = send(m, p, a)
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})
    return c, ok


def both(c):
    entries = c.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read",
                          "params": {"workspace": "w", "from": "human:ana"}})["result"]["entries"]
    return (frames(Chain(workspace="w", events=entries, state=None, source="audit.read")),
            frames(from_coordinator(c, workspace="w")))


def row(f, tid):
    return f.tasks.set_index("task_id").loc[tid]


def reviewed_task(ok, **extra):
    return ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x",
                              "review_required": True, **extra})["task_id"]


def escalate(ok, original):
    return ok("escalate.raise", {"original_task_id": original,
                                 "new_task": {"kind": "k", "assignee": "agent:y", "input": {}}},
              "agent:x")["new_task_id"]


def complete(ok, tid):
    ok("task.complete", {"task_id": tid, "output": {"d": 1}}, "agent:y")


def test_an_escalated_task_keeps_its_review_in_both_reads():
    c, ok = build()
    successor = escalate(ok, reviewed_task(ok))
    complete(ok, successor)

    env, state = both(c)
    for label, f in (("envelopes", env), ("state", state)):
        r = row(f, successor)
        assert r["review_required"] == True, label  # noqa: E712
        assert r["state"] == "review_requested", label
        assert r["n_reviews"] == 1, label


def test_a_superseded_task_keeps_its_review_in_both_reads():
    # The successor's own review_required: false cannot remove the
    # requirement the original carried.
    c, ok = build()
    original = reviewed_task(ok)
    successor = ok("control.supersede", {
        "task_id": original, "reason": "redo",
        "successor_task": {"kind": "v2", "assignee": "agent:y", "input": {},
                           "review_required": False}})["new_task_id"]
    complete(ok, successor)

    env, state = both(c)
    for label, f in (("envelopes", env), ("state", state)):
        r = row(f, successor)
        assert r["review_required"] == True, label  # noqa: E712
        assert r["state"] == "review_requested", label


def test_a_trial_successor_requires_review_under_modes():
    # The original is a trial task made before the workspace advertised
    # modes/1.0, so it carries no requirement. Its successor is made after,
    # and a trial task made then requires review.
    c, ok = build()
    original = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x",
                                  "mode": "trial"})["task_id"]
    ok("workspace.set_profiles", {"profiles": PROFILES + ["modes/1.0"]})
    successor = escalate(ok, original)
    complete(ok, successor)

    env, state = both(c)
    for label, f in (("envelopes", env), ("state", state)):
        assert pd.isna(row(f, original)["review_required"]), label
        r = row(f, successor)
        assert r["review_required"] == True, label  # noqa: E712
        assert r["state"] == "review_requested", label


def test_server_state_settles_a_successor_an_earlier_coordinator_completed():
    # An earlier coordinator left the successor without a requirement, so its
    # completion finished it, and the envelopes are the ones it wrote. Read
    # from them alone the successor looks as though it is awaiting review.
    # Server state shows no review was opened, and the read with it says so.
    c, ok = build()
    successor = escalate(ok, reviewed_task(ok))
    c.get_workspace("w").tasks[successor].review_required = None
    complete(ok, successor)

    _, state = both(c)
    r = row(state, successor)
    assert r["state"] == "completed"
    assert pd.isna(r["review_required"])
    assert r["outcome"] == "completed_without_review"
    assert r["n_reviews"] == 0
    assert not r["was_reviewed"]
