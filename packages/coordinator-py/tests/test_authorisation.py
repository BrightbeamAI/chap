"""
Authorisation tests: actor membership and reviewer-set eligibility.

These pin the fix for the gap a CHAP collaborator reported: decide.* (and
the adjacent actor-action methods) did not verify that `from` was a joined
member, so a decision could be attributed to a participant who never
joined. The reference implementations now enforce:

  - membership: `from` MUST be a joined workspace member (all actor-action
    methods); and
  - reviewer-set eligibility: to act on a review (decide.* / abstain),
    `from` MUST be one of the reviewers the review was addressed to.

SPECIFICATION.md S6.3 / S13.3 (`unknown_participant`). Surfaced via the
implementation's NOT_AUTHORISED code.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E


NOT_AUTHORISED = E.NOT_AUTHORISED


def coord() -> Coordinator:
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=["core/1.0", "review/1.0", "routing/1.0"],
    ))


def send(c: Coordinator, method: str, **params) -> dict:
    return c.dispatch({
        "jsonrpc": "2.0", "id": method, "method": method, "params": params,
    })


def _ready_review(c: Coordinator):
    """Set up a workspace with a task awaiting review by alice."""
    send(c, "workspace.create", workspace="w", profiles=["core/1.0", "review/1.0"])
    send(c, "participant.join", workspace="w", **{"from": "human:alice@x"}, type="human")
    send(c, "participant.join", workspace="w", **{"from": "agent:bot@x"}, type="agent")
    r = send(c, "task.create", workspace="w", **{"from": "human:alice@x"},
             kind="draft", assignee="agent:bot@x", input={"x": 1})
    tid = r["result"]["task_id"]
    send(c, "task.complete", workspace="w", **{"from": "agent:bot@x"},
         task_id=tid, output={"draft": "hi"})
    send(c, "review.request", workspace="w", **{"from": "agent:bot@x"},
         task_id=tid, to=["human:alice@x"], artefact={"draft": "hi"})
    return tid


# ---- membership floor --------------------------------------------


def test_decide_approve_by_non_member_is_rejected():
    c = coord()
    tid = _ready_review(c)
    r = send(c, "decide.approve", workspace="w",
             **{"from": "human:ghost@x"}, task_id=tid)
    assert "error" in r
    assert r["error"]["code"] == NOT_AUTHORISED


def test_decide_override_by_non_member_is_rejected():
    c = coord()
    tid = _ready_review(c)
    r = send(c, "decide.override", workspace="w",
             **{"from": "human:ghost@x"}, task_id=tid,
             diff=[{"op": "replace", "path": "/draft", "value": "x"}],
             rationale="should be rejected")
    assert "error" in r
    assert r["error"]["code"] == NOT_AUTHORISED


def test_task_complete_by_non_member_is_rejected():
    c = coord()
    send(c, "workspace.create", workspace="w", profiles=["core/1.0", "review/1.0"])
    send(c, "participant.join", workspace="w", **{"from": "human:alice@x"}, type="human")
    send(c, "participant.join", workspace="w", **{"from": "agent:bot@x"}, type="agent")
    r = send(c, "task.create", workspace="w", **{"from": "human:alice@x"},
             kind="draft", assignee="agent:bot@x", input={"x": 1})
    tid = r["result"]["task_id"]
    out = send(c, "task.complete", workspace="w",
               **{"from": "agent:ghost@x"}, task_id=tid, output={"draft": "x"})
    assert "error" in out
    assert out["error"]["code"] == NOT_AUTHORISED


def test_review_request_by_non_member_is_rejected():
    c = coord()
    send(c, "workspace.create", workspace="w", profiles=["core/1.0", "review/1.0"])
    send(c, "participant.join", workspace="w", **{"from": "human:alice@x"}, type="human")
    send(c, "participant.join", workspace="w", **{"from": "agent:bot@x"}, type="agent")
    r = send(c, "task.create", workspace="w", **{"from": "human:alice@x"},
             kind="draft", assignee="agent:bot@x", input={"x": 1})
    tid = r["result"]["task_id"]
    send(c, "task.complete", workspace="w", **{"from": "agent:bot@x"},
         task_id=tid, output={"draft": "x"})
    out = send(c, "review.request", workspace="w",
               **{"from": "agent:ghost@x"}, task_id=tid,
               to=["human:alice@x"], artefact={"draft": "x"})
    assert "error" in out
    assert out["error"]["code"] == NOT_AUTHORISED


# ---- reviewer-set eligibility ------------------------------------


def test_decide_by_member_not_in_reviewer_set_is_rejected():
    """A joined member who was not addressed in `to` cannot decide."""
    c = coord()
    tid = _ready_review(c)  # addressed to human:alice@x
    # Add a second human who is a member but not an addressed reviewer.
    send(c, "participant.join", workspace="w", **{"from": "human:bob@x"}, type="human")
    r = send(c, "decide.approve", workspace="w",
             **{"from": "human:bob@x"}, task_id=tid)
    assert "error" in r
    assert r["error"]["code"] == NOT_AUTHORISED


def test_abstain_by_member_not_in_reviewer_set_is_rejected():
    c = coord()
    tid = _ready_review(c)
    send(c, "participant.join", workspace="w", **{"from": "human:bob@x"}, type="human")
    r = send(c, "abstain.declare", workspace="w",
             **{"from": "human:bob@x"}, task_id=tid, reason="not mine to judge")
    assert "error" in r
    assert r["error"]["code"] == NOT_AUTHORISED


# ---- happy paths still work --------------------------------------


def test_addressed_reviewer_can_approve():
    c = coord()
    tid = _ready_review(c)
    r = send(c, "decide.approve", workspace="w",
             **{"from": "human:alice@x"}, task_id=tid)
    assert "error" not in r
    assert r["result"]["state"] == "completed"


def test_addressed_reviewer_can_override():
    c = coord()
    tid = _ready_review(c)
    r = send(c, "decide.override", workspace="w",
             **{"from": "human:alice@x"}, task_id=tid,
             diff=[{"op": "replace", "path": "/draft", "value": "edited"}],
             rationale="warmer phrasing", tags=["tone"])
    assert "error" not in r
    assert r["result"]["applied"] == {"draft": "edited"}


# ---- broadcast-scoped reviewer addressing ------------------------


def _ready_review_to(c: Coordinator, to):
    """Task awaiting review, addressed to the given `to` value."""
    send(c, "workspace.create", workspace="w", profiles=["core/1.0", "review/1.0"])
    send(c, "participant.join", workspace="w", **{"from": "human:alice@x"}, type="human")
    send(c, "participant.join", workspace="w", **{"from": "agent:bot@x"}, type="agent")
    r = send(c, "task.create", workspace="w", **{"from": "human:alice@x"},
             kind="draft", assignee="agent:bot@x", input={"x": 1})
    tid = r["result"]["task_id"]
    send(c, "task.complete", workspace="w", **{"from": "agent:bot@x"},
         task_id=tid, output={"draft": "hi"})
    send(c, "review.request", workspace="w", **{"from": "agent:bot@x"},
         task_id=tid, to=to, artefact={"draft": "hi"})
    return tid


def test_workspace_scoped_review_lets_any_member_decide():
    """A review addressed to `workspace:<id>` means any member may decide.

    This is the documented broadcast pattern (examples/03-review-and-approve.md);
    the reviewer-set check must not reject a real member here.
    """
    c = coord()
    tid = _ready_review_to(c, ["workspace:w"])
    r = send(c, "decide.approve", workspace="w",
             **{"from": "human:alice@x"}, task_id=tid)
    assert "error" not in r, f"workspace-scoped review should accept a member: {r.get('error')}"
    assert r["result"]["state"] == "completed"


def test_group_scoped_review_lets_a_member_decide():
    c = coord()
    tid = _ready_review_to(c, ["group:reviewers"])
    r = send(c, "decide.approve", workspace="w",
             **{"from": "human:alice@x"}, task_id=tid)
    assert "error" not in r
    assert r["result"]["state"] == "completed"


def test_workspace_scoped_review_still_rejects_a_non_member():
    """Broadcast scope relaxes the reviewer-set check, not the membership floor."""
    c = coord()
    tid = _ready_review_to(c, ["workspace:w"])
    r = send(c, "decide.approve", workspace="w",
             **{"from": "human:ghost@x"}, task_id=tid)
    assert "error" in r
    assert r["error"]["code"] == NOT_AUTHORISED
