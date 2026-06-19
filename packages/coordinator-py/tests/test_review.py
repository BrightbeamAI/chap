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
