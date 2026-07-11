"""
Tests for chap-google-adk. They run without google-adk installed: the bridge
reads the call and confirmation structurally, so small stand-ins (and plain
dicts) drive the same code path the real ADK objects would.

    1. A paused tool call under review -> task.create + complete + review.request.
    2. A resolved confirmation -> decide.approve | decide.override | decide.reject.
    3. The audit chain carries the whole trail, hash-linked.
"""

import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_google_adk import ChapConfirmationBridge, ChapBridgeError


class Call:
    """A paused FunctionCall stand-in (attribute access, like the real one)."""

    def __init__(self, name, args, id="fc-1"):
        self.name = name
        self.args = args
        self.id = id


class Conf:
    """A ToolConfirmation stand-in."""

    def __init__(self, confirmed, payload=None):
        self.confirmed = confirmed
        self.payload = payload


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, enable_chain=True,
    ))


@pytest.fixture
def bridge(coord):
    return ChapConfirmationBridge(
        coord,
        workspace="wsp_adk_test",
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
    ws = coord.workspaces["wsp_adk_test"]
    assert "agent:assistant#v1" in ws.members
    assert "human:alice@example.org" in ws.members


# ---- approve / reject derived from `confirmed` ---------------------


def test_confirmed_is_approve(bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    bridge.record_decision(Call("transfer", {"amount": 100}), Conf(True))
    assert seen == ["task.create", "task.complete", "review.request", "decide.approve"]


def test_not_confirmed_is_reject(bridge):
    bridge.record_decision(Call("transfer", {"amount": 100}), Conf(False))
    assert methods(bridge)[-1] == "decide.reject"


def test_explicit_decision_overrides_the_flag(bridge):
    # confirmed=True, but the caller says reject -> reject wins.
    bridge.record_decision(Call("transfer", {"amount": 100}), Conf(True),
                           decision="reject", rationale="not this recipient")
    assert methods(bridge)[-1] == "decide.reject"


# ---- override is explicit, never inferred from payload -------------


def test_confirmed_with_payload_is_still_approve(bridge):
    # A payload is tool-defined data, not the edited args. It must NOT be
    # promoted to an override.
    bridge.record_decision(Call("book_leave", {"days": 5}),
                           Conf(True, payload={"approved_days": 3}))
    assert methods(bridge)[-1] == "decide.approve"


def test_override_uses_returned_not_payload(bridge):
    bridge.record_decision(
        Call("transfer", {"amount": 100, "to": "acct-9"}),
        Conf(True, payload={"anything": "tool-defined"}),
        decision="override", returned={"amount": 50, "to": "acct-9"},
        rationale="capped", tags=["limit"],
    )
    params = last_params(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "/args/amount", "value": 50}]
    assert params["intent_preserved"] is True


def test_override_intent_preserved_false(bridge):
    bridge.record_decision(
        Call("transfer", {"to": "acct-9"}), Conf(True),
        decision="override", returned={"to": "acct-escrow"}, intent_preserved=False,
    )
    assert last_params(bridge, "decide.override")["intent_preserved"] is False


def test_override_without_edit_raises(bridge):
    with pytest.raises(ValueError, match="edited args"):
        bridge.record_decision(Call("transfer", {"amount": 100}), Conf(True),
                               decision="override")


def test_override_with_no_change_is_an_approve(bridge):
    bridge.record_decision(
        Call("transfer", {"amount": 100}), Conf(True),
        decision="override", returned={"amount": 100},
    )
    assert methods(bridge)[-1] == "decide.approve"


# ---- structural reads ----------------------------------------------


def test_dict_shaped_call_and_confirmation(bridge):
    bridge.record_decision({"name": "ship", "args": {"env": "prod"}, "id": "fc-9"},
                           {"confirmed": False})
    assert "decide.reject" in methods(bridge)
    assert last_params(bridge, "review.request")["artefact"]["tool"] == "ship"


def test_artefact_is_the_tool_call(bridge):
    bridge.record_decision(Call("send_email", {"to": "x@y.z"}), Conf(True))
    art = last_params(bridge, "review.request")["artefact"]
    assert art == {"tool": "send_email", "args": {"to": "x@y.z"}, "tool_call_id": "fc-1"}


def test_per_decision_approver_type_from_uri(coord, bridge):
    bridge.record_decision(Call("ship", {"env": "prod"}), Conf(True),
                           approver="service:autodeploy")
    ws = coord.workspaces["wsp_adk_test"]
    assert ws.members["service:autodeploy"].type == "service"
    assert last_params(bridge, "decide.approve")["from"] == "service:autodeploy"


# ---- guards --------------------------------------------------------


def test_unknown_decision_raises(bridge):
    with pytest.raises(ValueError, match="approve"):
        bridge.record_decision(Call("x", {}), Conf(True), decision="maybe")


def test_chain_is_hash_linked(bridge):
    bridge.record_decision(Call("a", {}), Conf(True))
    bridge.record_decision(Call("b", {}), Conf(False))
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
