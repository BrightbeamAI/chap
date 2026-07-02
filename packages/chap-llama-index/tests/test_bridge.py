"""
Tests for chap-llama-index. They run without llama-index-workflows: the
bridge reads events via event.get(...), so a plain dict stands in for an
InputRequiredEvent or HumanResponseEvent.

    1. A step proposes output -> task.create + complete + review.request.
    2. A human answers -> decide.approve | decide.override | decide.reject.
    3. The audit chain carries the whole trail, hash-linked.
"""

import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_llama_index import ChapHitlBridge, ChapBridgeError


def ask(proposed, **extra):
    return {"proposed": proposed, **extra}


def reply(**fields):
    return dict(fields)


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, enable_chain=True,
    ))


@pytest.fixture
def bridge(coord):
    return ChapHitlBridge(
        coord,
        workspace="wsp_li_test",
        agent="agent:writer#v1",
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
    ws = coord.workspaces["wsp_li_test"]
    assert "agent:writer#v1" in ws.members
    assert "human:alice@example.org" in ws.members


def test_bridge_is_idempotent(coord, bridge):
    ChapHitlBridge(coord, workspace="wsp_li_test",
                   agent="agent:writer#v1", reviewer="human:alice@example.org")
    assert len(coord.workspaces["wsp_li_test"].members) == 2


# ---- the three decisions -------------------------------------------


def test_approve(bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    bridge.record_decision(ask({"reply": "hi"}), reply(response="ok"), decision="approve")

    assert seen == ["task.create", "task.complete", "review.request", "decide.approve"]
    assert methods(bridge)[-1] == "decide.approve"


def test_override_diffs_proposed_vs_returned(bridge):
    bridge.record_decision(
        ask({"amount": 100, "to": "acct-9"}),
        reply(response={"amount": 50, "to": "acct-9"}, rationale="capped", tags=["limit"]),
        decision="override",
    )
    params = last_params(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "/amount", "value": 50}]
    assert params["rationale"] == "capped"
    assert params["tags"] == ["limit"]
    assert params["intent_preserved"] is True


def test_override_intent_preserved_from_response(bridge):
    bridge.record_decision(
        ask({"to": "acct-9"}),
        reply(response={"to": "acct-escrow"}, intent_preserved=False),
        decision="override",
    )
    assert last_params(bridge, "decide.override")["intent_preserved"] is False


def test_override_with_no_change_is_an_approve(bridge):
    bridge.record_decision(
        ask({"amount": 100}), reply(response={"amount": 100}), decision="override",
    )
    assert methods(bridge)[-1] == "decide.approve"


def test_reject_uses_response_as_note(bridge):
    bridge.record_decision(
        ask({"reply": "rude"}), reply(response="tone is off"), decision="reject",
    )
    assert last_params(bridge, "decide.reject")["comment"] == "tone is off"


# ---- reading fields and overrides ----------------------------------


def test_explicit_proposed_overrides_the_event(bridge):
    bridge.record_decision(
        ask({"ignored": True}), reply(response="ok"),
        decision="approve", proposed={"real": "artefact"},
    )
    assert last_params(bridge, "review.request")["artefact"] == {"real": "artefact"}


def test_per_decision_approver_from_response(coord, bridge):
    bridge.record_decision(
        ask({"x": 1}), reply(response="ok", user_name="service:autodeploy"),
        decision="approve",
    )
    ws = coord.workspaces["wsp_li_test"]
    assert ws.members["service:autodeploy"].type == "service"
    assert last_params(bridge, "decide.approve")["from"] == "service:autodeploy"


# ---- guards --------------------------------------------------------


def test_unknown_decision_raises(bridge):
    with pytest.raises(ValueError, match="approve"):
        bridge.record_decision(ask({"x": 1}), reply(response="?"), decision="maybe")


def test_chain_is_hash_linked(bridge):
    bridge.record_decision(ask({"x": 1}), reply(response="ok"), decision="approve")
    bridge.record_decision(ask({"x": 2}), reply(response="no"), decision="reject")
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
