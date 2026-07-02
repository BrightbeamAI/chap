"""
Tests for chap-ag2. They run without AG2 installed: the bridge reads the
message and reply as plain values, so a dict message and a string reply
stand in for a real AG2 turn.

    1. An agent message under review -> task.create + complete + review.request.
    2. A human turn -> decide.approve | decide.override | decide.reject
       (or nothing, for plain dialogue).
    3. The audit chain carries the whole trail, hash-linked.
"""

import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_ag2 import ChapTurnBridge, ChapBridgeError


def msg(content, name="assistant"):
    return {"content": content, "role": "user", "name": name}


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, enable_chain=True,
    ))


@pytest.fixture
def bridge(coord):
    return ChapTurnBridge(
        coord,
        workspace="wsp_ag2_test",
        agent="agent:assistant#v1",
        reviewer="human:alice@example.org",
    )


def methods(bridge):
    return [e["envelope"]["method"] for e in bridge.audit()]


def last_params(bridge, method):
    for entry in reversed(bridge.audit()):
        if entry["envelope"]["method"] == method:
            return entry["envelope"]["params"]
    raise AssertionError(f"no {method} in audit")


# ---- setup ---------------------------------------------------------


def test_bridge_joins_agent_and_reviewer(coord, bridge):
    ws = coord.workspaces["wsp_ag2_test"]
    assert "agent:assistant#v1" in ws.members
    assert "human:alice@example.org" in ws.members


# ---- the safe fallback: only empty -> approve ----------------------


def test_empty_reply_infers_approve(bridge):
    tid = bridge.record_turn(msg("draft"), "")
    assert tid is not None
    assert methods(bridge)[-1] == "decide.approve"


def test_non_empty_reply_without_decision_records_nothing(bridge):
    tid = bridge.record_turn(msg("draft"), "what about the tax?")
    assert tid is None
    assert methods(bridge) == ["workspace.create", "participant.join", "participant.join"]


def test_exit_is_not_a_reject(bridge):
    tid = bridge.record_turn(msg("draft"), "exit")
    assert tid is None
    assert "decide.reject" not in methods(bridge)


# ---- explicit decisions --------------------------------------------


def test_explicit_approve(bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    bridge.record_turn(msg("draft"), "", decision="approve")
    assert seen == ["task.create", "task.complete", "review.request", "decide.approve"]


def test_override_diffs_message_vs_reply(bridge):
    bridge.record_turn(
        msg("refund $100 to Alice"), "refund $50 to Alice",
        decision="override", rationale="over the limit", tags=["capped"],
    )
    params = last_params(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "", "value": "refund $50 to Alice"}]
    assert params["rationale"] == "over the limit"
    assert params["intent_preserved"] is True


def test_override_intent_preserved_false(bridge):
    bridge.record_turn(msg("ship it"), "hold the release",
                       decision="override", intent_preserved=False)
    assert last_params(bridge, "decide.override")["intent_preserved"] is False


def test_override_with_no_change_is_an_approve(bridge):
    bridge.record_turn(msg("keep as is"), "keep as is", decision="override")
    assert methods(bridge)[-1] == "decide.approve"


def test_explicit_reject_uses_reply_as_note(bridge):
    bridge.record_turn(msg("rude draft"), "tone is unacceptable", decision="reject")
    assert last_params(bridge, "decide.reject")["comment"] == "tone is unacceptable"


# ---- artefact and identity -----------------------------------------


def test_message_content_is_the_artefact(bridge):
    bridge.record_turn(msg("the draft body"), "", decision="approve")
    assert last_params(bridge, "review.request")["artefact"] == "the draft body"


def test_per_turn_approver_from_uri_scheme(coord, bridge):
    bridge.record_turn(msg("draft"), "", decision="approve",
                       approver="service:autodeploy")
    ws = coord.workspaces["wsp_ag2_test"]
    assert ws.members["service:autodeploy"].type == "service"
    assert last_params(bridge, "decide.approve")["from"] == "service:autodeploy"


# ---- guards --------------------------------------------------------


def test_unknown_decision_raises(bridge):
    with pytest.raises(ValueError, match="approve"):
        bridge.record_turn(msg("draft"), "x", decision="maybe")


def test_chain_is_hash_linked(bridge):
    bridge.record_turn(msg("a"), "", decision="approve")
    bridge.record_turn(msg("b"), "no good", decision="reject")
    audit = bridge.audit()
    assert len(audit) >= 8
    assert all("prev_hash" in e for e in audit)


def test_coordinator_error_surfaces(bridge):
    with pytest.raises(ChapBridgeError):
        bridge._dispatch("decide.approve", {
            "workspace": bridge.workspace,
            "from": bridge.reviewer,
            "task_id": "tsk_does_not_exist",
        })
