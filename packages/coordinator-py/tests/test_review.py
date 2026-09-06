"""Tests for the review/1.0 profile."""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions


@pytest.fixture
def coord_with_task():
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
    ))

    def send(method, **params):
        return coord.dispatch({
            "jsonrpc": "2.0", "id": f"t-{method}", "method": method, "params": params,
        })

    send("workspace.create", workspace="wsp_r")
    send("participant.join", workspace="wsp_r",
         **{"from": "human:alice@x", "type": "human", "role": "reviewer"})
    send("participant.join", workspace="wsp_r",
         **{"from": "agent:bot", "type": "agent", "role": "drafter"})
    r = send("task.create", workspace="wsp_r",
             **{"from": "human:alice@x", "kind": "draft", "input": {}},
             assignee="agent:bot")
    return coord, send, r["result"]["task_id"]


def test_override_with_diff(coord_with_task):
    coord, send, tid = coord_with_task
    draft = {"severity": "warning", "text": "issue"}
    send("review.request", workspace="wsp_r", task_id=tid,
         **{"from": "agent:bot", "to": "human:alice@x", "artefact": draft})
    r = send("decide.override", workspace="wsp_r", task_id=tid,
             **{"from": "human:alice@x"},
             diff=[{"op": "replace", "path": "/severity", "value": "info"}],
             rationale="false positive",
             tags=["false-positive"])
    assert "result" in r
    assert r["result"]["applied"]["severity"] == "info"


def test_override_carries_intent_preserved(coord_with_task):
    coord, send, tid = coord_with_task
    draft = {"severity": "warning"}
    send("review.request", workspace="wsp_r", task_id=tid,
         **{"from": "agent:bot", "to": "human:alice@x", "artefact": draft})
    r = send("decide.override", workspace="wsp_r", task_id=tid,
             **{"from": "human:alice@x"},
             diff=[{"op": "replace", "path": "/severity", "value": "info"}],
             rationale="cosmetic only",
             tags=[],
             intent_preserved=True,
             logical_id="lgl_abc123")
    art_id = r["result"]["override_artefact_id"]
    ws = coord.workspaces["wsp_r"]
    override = ws.overrides[art_id]
    assert override.intent_preserved is True
    assert override.logical_id == "lgl_abc123"


def test_override_rejects_invalid_patch(coord_with_task):
    coord, send, tid = coord_with_task
    send("review.request", workspace="wsp_r", task_id=tid,
         **{"from": "agent:bot", "to": "human:alice@x", "artefact": {"a": 1}})
    r = send("decide.override", workspace="wsp_r", task_id=tid,
             **{"from": "human:alice@x"},
             diff=[{"op": "replace", "path": "/nonexistent", "value": 2}],
             rationale="x", tags=[])
    assert r["error"]["code"] == -32012  # PATCH_FAILED


def test_decide_override_requires_review_state(coord_with_task):
    coord, send, tid = coord_with_task
    # No review.request: task is in 'created'
    r = send("decide.override", workspace="wsp_r", task_id=tid,
             **{"from": "human:alice@x"},
             diff=[], rationale="x", tags=[])
    assert r["error"]["code"] == -32010  # NOT_REVIEWABLE


def test_abstain_declare(coord_with_task):
    coord, send, tid = coord_with_task
    send("review.request", workspace="wsp_r", task_id=tid,
         **{"from": "agent:bot", "to": "human:alice@x", "artefact": {}})
    r = send("abstain.declare", workspace="wsp_r", task_id=tid,
             **{"from": "human:alice@x"},
             reason="conflict of interest",
             category="conflict_of_interest")
    assert r["result"]["state"] == "abstained"


def test_escalate_raise(coord_with_task):
    coord, send, tid = coord_with_task
    send("participant.join", workspace="wsp_r",
         **{"from": "human:senior@x", "type": "human", "role": "lead"})
    r = send("escalate.raise", workspace="wsp_r",
             **{"from": "human:alice@x"},
             original_task_id=tid,
             new_task={"kind": "review", "input": {"reason": "high-risk"},
                       "assignee": "human:senior@x"})
    assert "result" in r
    assert r["result"]["escalated_from"] == tid


def test_task_update_cannot_complete_task_awaiting_review(coord_with_task):
    coord, send, tid = coord_with_task
    send("review.request", workspace="wsp_r", task_id=tid,
         **{"from": "agent:bot", "to": "human:alice@x", "artefact": {"text": "draft"}})

    # The drafter tries to self-complete the task under review, with no reviewer
    # decision. The review gate lives in decide.*/abstain (reviewer-gated); a plain
    # task.update by a member must not reach a terminal state around it.
    bypass = send("task.update", workspace="wsp_r", task_id=tid,
                  **{"from": "agent:bot", "state": "completed"})
    assert "error" in bypass
    assert coord.get_workspace("wsp_r").tasks[tid].state == "review_requested"

    # Withdrawing the review request back to in_progress stays legal.
    withdraw = send("task.update", workspace="wsp_r", task_id=tid,
                    **{"from": "agent:bot", "state": "in_progress"})
    assert withdraw["result"]["state"] == "in_progress"


def test_task_complete_opens_review_addressed_to_eligible_reviewer():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))

    def send(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"t-{method}",
                               "method": method, "params": params})

    send("workspace.create", workspace="w",
         profiles=["core/1.0", "review/1.0", "modes/1.0"])
    send("participant.join", workspace="w",
         **{"from": "human:alice", "type": "human", "role": "reviewer"})
    send("participant.join", workspace="w",
         **{"from": "agent:bot", "type": "agent", "role": "drafter"})
    tid = send("task.create", workspace="w",
               **{"from": "human:alice", "kind": "draft", "input": {},
                  "assignee": "agent:bot", "mode": "trial"})["result"]["task_id"]
    assert coord.workspaces["w"].tasks[tid].review_required is True
    send("task.update", workspace="w", **{"from": "agent:bot"},
         task_id=tid, state="in_progress")

    # task.complete opens a review addressed to the eligible reviewer (excluding
    # the producer/assignee); it does not complete.
    r = send("task.complete", workspace="w", **{"from": "agent:bot"},
             task_id=tid, output={"draft": "unreviewed"})
    assert r["result"]["state"] == "review_requested"
    t = coord.workspaces["w"].tasks[tid]
    assert t.state == "review_requested"
    assert t.output is None
    assert t.review.requested_to == ["human:alice"]

    # The eligible reviewer completes it; the producer could not have.
    a = send("decide.approve", workspace="w", **{"from": "human:alice"}, task_id=tid)
    assert a["result"]["state"] == "completed"
    assert coord.workspaces["w"].tasks[tid].output == {"draft": "unreviewed"}


def test_task_complete_refused_when_no_eligible_reviewer():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))

    def send(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"t-{method}",
                               "method": method, "params": params})

    send("workspace.create", workspace="w",
         profiles=["core/1.0", "review/1.0", "modes/1.0"])
    send("participant.join", workspace="w",
         **{"from": "agent:bot", "type": "agent", "role": "drafter"})
    tid = send("task.create", workspace="w",
               **{"from": "agent:bot", "kind": "draft", "input": {},
                  "assignee": "agent:bot", "mode": "trial"})["result"]["task_id"]
    send("task.update", workspace="w", **{"from": "agent:bot"},
         task_id=tid, state="in_progress")

    # The only member is both producer and assignee, so nobody can review. The
    # producer must not self-approve, so completion is refused, not opened.
    r = send("task.complete", workspace="w", **{"from": "agent:bot"},
             task_id=tid, output={"x": 1})
    assert r["error"]["code"] == -32011  # NOT_AUTHORISED
    assert coord.workspaces["w"].tasks[tid].state == "in_progress"


def test_trial_does_not_force_review_without_modes_profile():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))

    def send(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"t-{method}",
                               "method": method, "params": params})

    # Default profiles are core + review, with no modes/1.0.
    send("workspace.create", workspace="w")
    send("participant.join", workspace="w",
         **{"from": "human:alice", "type": "human", "role": "owner"})
    send("participant.join", workspace="w",
         **{"from": "agent:bot", "type": "agent", "role": "drafter"})
    tid = send("task.create", workspace="w",
               **{"from": "human:alice", "kind": "draft", "input": {},
                  "assignee": "agent:bot", "mode": "trial"})["result"]["task_id"]
    # modes/1.0 is not loaded, so trial is inert: review is not forced.
    assert coord.workspaces["w"].tasks[tid].review_required is None
    send("task.update", workspace="w", **{"from": "agent:bot"},
         task_id=tid, state="in_progress")
    r = send("task.complete", workspace="w", **{"from": "agent:bot"},
             task_id=tid, output={"ok": 1})
    assert r["result"]["state"] == "completed"
