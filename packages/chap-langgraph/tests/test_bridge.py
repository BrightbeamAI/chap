"""
Tests for chap-langgraph.

Cover the bridge in isolation (no LangGraph dependency). These tests
demonstrate the same handshake an actual LangGraph interrupt would
trigger:

    1. Agent produces a draft -> hil_review() records task.create + review.request.
    2. Workflow pauses. Human decides.
    3. Decision payload flows back -> apply_decision() records the matching envelope.
    4. Audit chain has the full trail with hash linkage.

tests/test_graph.py runs the same bridge in a real LangGraph graph.
"""

import sys
import threading
from pathlib import Path

# Make the local package importable without an install step.
THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions, content_hash
from chap_langgraph import (
    ChapBridge, ChapBridgeError, Decision, ReviewRejected, hil_review,
)


REVIEWER = "human:alice@example.org"


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
        reviewer=REVIEWER,
    )


def methods(bridge: ChapBridge) -> list[str]:
    return [(e.get("envelope") or e["request"])["method"] for e in bridge.audit()]


def last_params(bridge: ChapBridge, method: str) -> dict:
    for entry in reversed(bridge.audit()):
        env = entry.get("envelope") or entry["request"]
        if env["method"] == method:
            return env["params"]
    raise AssertionError(f"no {method} in audit")


def desk_decides(coord: Coordinator, method: str, task_id: str, **params) -> dict:
    """A decision made outside the bridge, as a desk sharing the
    coordinator would make it: the person joins, then decides."""
    coord.dispatch({"jsonrpc": "2.0", "id": "join", "method": "participant.join",
                    "params": {"workspace": "wsp_lg_test", "from": REVIEWER,
                               "type": "human"}})
    out = coord.dispatch({"jsonrpc": "2.0", "id": "desk", "method": method,
                          "params": {"workspace": "wsp_lg_test", "from": REVIEWER,
                                     "task_id": task_id, **params}})
    assert "error" not in out, out
    return out["result"]


# ---- core bridge behaviour -----------------------------------------


def test_bridge_creates_workspace_and_joins_the_agent(coord: Coordinator,
                                                      bridge: ChapBridge) -> None:
    ws = coord.workspaces["wsp_lg_test"]
    assert "agent:drafter#v1" in ws.members
    # The reviewer joins when a decision is first recorded in their name, so
    # a person deciding at a desk joins there, with their own key.
    assert REVIEWER not in ws.members


def test_bridge_idempotent(coord: Coordinator, bridge: ChapBridge) -> None:
    """Re-instantiating the bridge on the same workspace should not error."""
    second = ChapBridge(
        coord,
        workspace="wsp_lg_test",
        agent="agent:drafter#v1",
        reviewer=REVIEWER,
    )
    # Both bridges should reach the same workspace.
    assert second.workspace == bridge.workspace
    ws = coord.workspaces["wsp_lg_test"]
    assert len(ws.members) == 1


def test_hil_review_opens_a_review_the_task_requires(bridge: ChapBridge,
                                                     coord: Coordinator) -> None:
    seen: list[str] = []
    bridge.on_envelope = lambda m, p: seen.append(m)

    state = hil_review(bridge, artefact={"reply": "draft"}, kind="draft_response")

    # The draft is never recorded as a completed task: the review opens on
    # the created task, and only the reviewer's decision completes it.
    assert seen == ["task.create", "review.request"]
    task = coord.workspaces["wsp_lg_test"].tasks[state["chap_task_id"]]
    assert task.review_required is True
    assert task.state == "review_requested"
    assert task.review.requested_to == [REVIEWER]
    assert state["chap_artefact"] == {"reply": "draft"}
    assert state["chap_artefact_digest"] == content_hash({"reply": "draft"})


def test_apply_decision_approve_returns_the_draft_shown(bridge: ChapBridge,
                                                        coord: Coordinator) -> None:
    state = hil_review(bridge, artefact={"reply": "draft"}, kind="draft_response")
    result = bridge.apply_decision(state["chap_task_id"], "approve",
                                   state["chap_artefact"])
    assert result == {"reply": "draft"}

    assert methods(bridge)[-1] == "decide.approve"
    params = last_params(bridge, "decide.approve")
    assert params["from"] == REVIEWER
    assert params["approved_artefact_digest"] == content_hash({"reply": "draft"})
    task = coord.workspaces["wsp_lg_test"].tasks[state["chap_task_id"]]
    assert (task.state, task.output) == ("completed", {"reply": "draft"})
    assert coord.workspaces["wsp_lg_test"].members[REVIEWER].type == "human"


def test_apply_decision_reject_raises_and_carries_nothing_on(bridge: ChapBridge,
                                                             coord: Coordinator) -> None:
    state = hil_review(bridge, artefact={"reply": "rude"}, kind="draft")
    with pytest.raises(ReviewRejected) as raised:
        bridge.apply_decision(state["chap_task_id"],
                              {"action": "reject", "rationale": "tone too rude"},
                              state["chap_artefact"])

    assert raised.value.task_id == state["chap_task_id"]
    assert raised.value.rationale == "tone too rude"
    assert raised.value.reviewer == REVIEWER
    assert methods(bridge)[-1] == "decide.reject"
    # The note goes where the desk and chap-analytics read a reviewer's note.
    assert last_params(bridge, "decide.reject")["comment"] == "tone too rude"
    task = coord.workspaces["wsp_lg_test"].tasks[state["chap_task_id"]]
    assert task.state == "declined"
    assert task.output is None


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
    }, state["chap_artefact"])
    assert applied == {"reply": "I sincerely apologise."}

    assert methods(bridge)[-1] == "decide.override"
    params = last_params(bridge, "decide.override")
    assert params["approved_artefact_digest"] == content_hash({"reply": "Sorry."})


def test_audit_chain_is_hash_linked(bridge: ChapBridge,
                                    coord: Coordinator) -> None:
    """End-to-end: every entry should chain via prev_hash."""
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    bridge.apply_decision(state["chap_task_id"], "approve", state["chap_artefact"])

    audit = bridge.audit()
    assert len(audit) >= 5  # workspace.create + 2x join + create/request + approve

    # First entry has the zero hash; subsequent entries chain.
    for i, entry in enumerate(audit):
        assert "prev_hash" in entry, f"entry {i} missing prev_hash"


# ---- the decision binds to the draft shown --------------------------


def test_a_decision_on_another_draft_is_refused(bridge: ChapBridge,
                                                coord: Coordinator) -> None:
    """A decision applied with a draft other than the one under review is
    refused, and nothing is decided."""
    state = hil_review(bridge, {"reply": "We will refund you 100 EUR."}, "draft")
    with pytest.raises(ChapBridgeError) as raised:
        bridge.apply_decision(state["chap_task_id"], "approve",
                              {"reply": "We will refund you 1000 EUR."})
    assert raised.value.code == -32074
    task = coord.workspaces["wsp_lg_test"].tasks[state["chap_task_id"]]
    assert task.state == "review_requested"


def test_a_digest_sent_back_by_the_interface_is_carried(bridge: ChapBridge,
                                                        coord: Coordinator) -> None:
    """An interface that sends back the digest of what it showed binds the
    decision to that: a digest of another draft is refused."""
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    with pytest.raises(ChapBridgeError) as raised:
        bridge.apply_decision(state["chap_task_id"], {
            "action": "approve",
            "approved_artefact_digest": content_hash({"reply": "another draft"}),
        }, state["chap_artefact"])
    assert raised.value.code == -32074

    bridge.apply_decision(state["chap_task_id"], {
        "action": "approve",
        "approved_artefact_digest": state["chap_artefact_digest"],
    }, state["chap_artefact"])
    assert coord.workspaces["wsp_lg_test"].tasks[state["chap_task_id"]].state == "completed"


# ---- a decision made at a desk ---------------------------------------


def test_wait_for_decision_reads_a_desk_approval(bridge: ChapBridge,
                                                 coord: Coordinator) -> None:
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    timer = threading.Timer(0.05, desk_decides,
                            (coord, "decide.approve", state["chap_task_id"]),
                            {"comment": "fine"})
    timer.start()
    try:
        decision = bridge.wait_for_decision(state["chap_task_id"],
                                            timeout=5, poll_seconds=0.01)
    finally:
        timer.join()

    assert decision == Decision(task_id=state["chap_task_id"], state="completed",
                                kind="approve", reviewer=REVIEWER,
                                output={"reply": "draft"}, comment="fine")
    assert decision.approved
    # Applying a decision read from the desk records nothing more.
    before = len(bridge.audit())
    assert bridge.apply_decision(state["chap_task_id"], decision,
                                 state["chap_artefact"]) == {"reply": "draft"}
    assert len(bridge.audit()) == before


def test_a_desk_override_reaches_the_graph_with_its_rationale(bridge: ChapBridge,
                                                              coord: Coordinator) -> None:
    state = hil_review(bridge, {"reply": "Sorry."}, "draft")
    desk_decides(coord, "decide.override", state["chap_task_id"],
                 diff=[{"op": "replace", "path": "/reply", "value": "So sorry."}],
                 rationale="warmer")
    decision = bridge.wait_for_decision(state["chap_task_id"], timeout=1)
    assert (decision.kind, decision.output, decision.comment) == (
        "override", {"reply": "So sorry."}, "warmer")
    assert bridge.apply_decision(state["chap_task_id"], decision,
                                 state["chap_artefact"]) == {"reply": "So sorry."}


def test_a_desk_rejection_raises(bridge: ChapBridge, coord: Coordinator) -> None:
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    desk_decides(coord, "decide.reject", state["chap_task_id"],
                 comment="try again", request_revision=True)
    decision = bridge.wait_for_decision(state["chap_task_id"], timeout=1)
    assert not decision.approved and decision.revision_requested
    assert decision.output is None
    with pytest.raises(ReviewRejected) as raised:
        bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"])
    assert (raised.value.state, raised.value.rationale) == ("in_progress", "try again")


def test_wait_for_decision_reads_a_client_with_task(coord: Coordinator) -> None:
    """Any client whose task(task_id) returns the GET /api/tasks/<id> view."""

    class DeskClient:
        def __init__(self) -> None:
            self.reads = 0

        def task(self, task_id):
            self.reads += 1
            if self.reads < 3:
                return {"task_id": task_id, "state": "review_requested",
                        "output": None, "review": {"decisions": []}}
            return {"task_id": task_id, "state": "completed",
                    "output": {"reply": "edited"},
                    "review": {"decisions": [{
                        "reviewer": REVIEWER, "kind": "override", "tags": ["tone"],
                        "comment": "warmer", "rationale": "warmer",
                        "override_artefact_id": "art_1"}]}}

    client = DeskClient()
    bridge = ChapBridge(coord, workspace="wsp_lg_test", agent="agent:drafter#v1",
                        reviewer=REVIEWER, client=client)
    decision = bridge.wait_for_decision("tsk_remote", timeout=5, poll_seconds=0.01)
    assert client.reads == 3
    assert decision.approved
    assert (decision.kind, decision.output, decision.tags) == (
        "override", {"reply": "edited"}, ("tone",))


def test_wait_for_decision_times_out(bridge: ChapBridge) -> None:
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    with pytest.raises(TimeoutError):
        bridge.wait_for_decision(state["chap_task_id"], timeout=0.05, poll_seconds=0.01)


def test_wait_for_decision_on_an_unknown_task(bridge: ChapBridge) -> None:
    with pytest.raises(KeyError):
        bridge.wait_for_decision("tsk_none", timeout=1)


def test_wait_for_decision_needs_something_to_read(coord: Coordinator) -> None:
    class DispatchOnly:
        """Answers dispatch() and nothing else."""

        def __init__(self, inner):
            self.inner = inner
            self.workspaces = inner.workspaces

        def dispatch(self, envelope):
            return self.inner.dispatch(envelope)

    bridge = ChapBridge(DispatchOnly(coord), workspace="wsp_lg_test",
                        agent="agent:drafter#v1", reviewer=REVIEWER)
    with pytest.raises(TypeError, match="task\\(task_id\\)"):
        bridge.wait_for_decision("tsk_any", timeout=1)


# ---- error paths ---------------------------------------------------


def test_override_requires_diff(bridge: ChapBridge) -> None:
    state = hil_review(bridge, {"x": 1}, "draft")
    with pytest.raises(ValueError, match="requires a 'diff'"):
        bridge.apply_decision(state["chap_task_id"], {"action": "override"},
                              state["chap_artefact"])


def test_unknown_decision_action(bridge: ChapBridge) -> None:
    state = hil_review(bridge, {"x": 1}, "draft")
    with pytest.raises(ValueError, match="unknown decision action"):
        bridge.apply_decision(state["chap_task_id"], {"action": "frobnicate"},
                              state["chap_artefact"])


def test_chap_error_surfaces_through_bridge(bridge: ChapBridge) -> None:
    """An invalid override (bad diff) should surface CHAP's error code."""
    state = hil_review(bridge, {"x": 1}, "draft")
    with pytest.raises(ChapBridgeError):
        bridge.apply_decision(state["chap_task_id"], {
            "diff":      [{"op": "replace", "path": "/missing/path", "value": 1}],
            "rationale": "this will fail",
        }, state["chap_artefact"])


def test_ambiguous_decision_is_not_approved(bridge: ChapBridge) -> None:
    """A payload without an explicit action must not default to approve.

    A reject expressed under the wrong key (``{"decision": "reject"}``) used
    to be recorded as a decide.approve.
    """
    state = hil_review(bridge, {"reply": "rude"}, "draft")
    with pytest.raises(ValueError, match="explicit 'action'"):
        bridge.apply_decision(state["chap_task_id"], {"decision": "reject"},
                              state["chap_artefact"])


def test_non_human_reviewer_rejected(coord: Coordinator) -> None:
    with pytest.raises(ValueError, match="human:"):
        ChapBridge(
            coord,
            workspace="wsp_lg_bot",
            agent="agent:drafter#v1",
            reviewer="agent:bot#v1",
        )


def test_the_agent_cannot_review_its_own_draft(coord: Coordinator) -> None:
    with pytest.raises(ValueError, match="cannot be the agent"):
        ChapBridge(coord, workspace="wsp_lg_self",
                   agent="human:alice@example.org", reviewer="human:alice@example.org")
