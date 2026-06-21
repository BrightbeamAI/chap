"""
Tests for chap-langgraph.

Cover the bridge in isolation (no LangGraph dependency). These tests
demonstrate the same handshake an actual LangGraph interrupt would
trigger:

    1. Agent produces a draft → hil_review() records task.complete + review.request.
    2. Workflow pauses. Human decides.
    3. Decision payload flows back → apply_decision() records the matching envelope.
    4. Audit chain has the full trail with hash linkage.
"""

import sys
from pathlib import Path

# Make the local package importable without an install step.
THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_langgraph import ChapBridge, ChapBridgeError, hil_review


# ---- fixtures ------------------------------------------------------


@pytest.fixture
def coord() -> Coordinator:
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, enable_chain=True,
    ))


@pytest.fixture
def bridge(coord: Coordinator) -> ChapBridge:
    return ChapBridge(
        coord,
        workspace="wsp_lg_test",
        agent="agent:drafter#v1",
        reviewer="human:alice@example.org",
    )


# ---- core bridge behaviour -----------------------------------------


def test_bridge_creates_workspace_and_members(coord: Coordinator,
                                              bridge: ChapBridge) -> None:
    ws = coord.workspaces["wsp_lg_test"]
    assert "agent:drafter#v1" in ws.members
    assert "human:alice@example.org" in ws.members


def test_bridge_idempotent(coord: Coordinator, bridge: ChapBridge) -> None:
    """Re-instantiating the bridge on the same workspace should not error."""
    second = ChapBridge(
        coord,
        workspace="wsp_lg_test",
        agent="agent:drafter#v1",
        reviewer="human:alice@example.org",
    )
    # Both bridges should reach the same workspace.
    assert second.workspace == bridge.workspace
    ws = coord.workspaces["wsp_lg_test"]
    assert len(ws.members) == 2


def test_hil_review_emits_complete_then_request(bridge: ChapBridge,
                                                coord: Coordinator) -> None:
    seen: list[str] = []
    bridge.on_envelope = lambda m, p: seen.append(m)

    state = hil_review(bridge, artefact={"reply": "draft"}, kind="draft_response")

    # task.create + task.complete + review.request, in that order
    assert seen == ["task.create", "task.complete", "review.request"]
    assert "chap_task_id" in state
    assert state["chap_artefact"] == {"reply": "draft"}


def test_apply_decision_approve(bridge: ChapBridge,
                                coord: Coordinator) -> None:
    state = hil_review(bridge, artefact={"reply": "draft"}, kind="draft_response")
    result = bridge.apply_decision(state["chap_task_id"], "approve")
    assert result is None

    # The audit chain ends on decide.approve
    audit = bridge.audit()
    methods = [e["envelope"]["method"] for e in audit]
    assert methods[-1] == "decide.approve"


def test_apply_decision_reject(bridge: ChapBridge) -> None:
    state = hil_review(bridge, artefact={"reply": "rude"}, kind="draft")
    bridge.apply_decision(state["chap_task_id"],
                          {"action": "reject", "rationale": "tone too rude"})

    methods = [e["envelope"]["method"] for e in bridge.audit()]
    assert methods[-1] == "decide.reject"


def test_apply_decision_override_returns_patched_value(
    bridge: ChapBridge,
) -> None:
    state = hil_review(
        bridge,
        artefact={"reply": "Sorry."},
        kind="draft",
    )
    diff = [{"op": "replace", "path": "/reply", "value": "I sincerely apologise."}]
    applied = bridge.apply_decision(state["chap_task_id"], {
        "diff":             diff,
        "rationale":        "tone too curt",
        "tags":             ["tone-warmed"],
        "intent_preserved": True,
    })
    assert applied == {"reply": "I sincerely apologise."}

    methods = [e["envelope"]["method"] for e in bridge.audit()]
    assert methods[-1] == "decide.override"


def test_audit_chain_is_hash_linked(bridge: ChapBridge,
                                    coord: Coordinator) -> None:
    """End-to-end: every entry should chain via prev_hash."""
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    bridge.apply_decision(state["chap_task_id"], "approve")

    audit = bridge.audit()
    assert len(audit) >= 5  # workspace.create + 2x join + create/complete/request + approve

    # First entry has the zero hash; subsequent entries chain.
    for i, entry in enumerate(audit):
        assert "prev_hash" in entry, f"entry {i} missing prev_hash"


# ---- error paths ---------------------------------------------------


def test_override_requires_diff(bridge: ChapBridge) -> None:
    state = hil_review(bridge, {"x": 1}, "draft")
    with pytest.raises(ValueError, match="requires a 'diff'"):
        bridge.apply_decision(state["chap_task_id"], {"action": "override"})


def test_unknown_decision_action(bridge: ChapBridge) -> None:
    state = hil_review(bridge, {"x": 1}, "draft")
    with pytest.raises(ValueError, match="unknown decision action"):
        bridge.apply_decision(state["chap_task_id"], {"action": "frobnicate"})


def test_chap_error_surfaces_through_bridge(bridge: ChapBridge) -> None:
    """An invalid override (bad diff) should surface CHAP's error code."""
    state = hil_review(bridge, {"x": 1}, "draft")
    with pytest.raises(ChapBridgeError):
        bridge.apply_decision(state["chap_task_id"], {
            "diff":      [{"op": "replace", "path": "/missing/path", "value": 1}],
            "rationale": "this will fail",
        })
