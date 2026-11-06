"""
Tests for chap-google-adk. They run without google-adk installed: the bridge
reads the call and confirmation structurally, so small stand-ins (and plain
dicts) drive the same code path the real ADK objects would.

    1. A paused tool call under review -> task.create + complete + review.request.
    2. A resolved confirmation -> decide.approve | decide.override | decide.reject.
    3. The audit chain carries the whole trail, hash-linked.
"""

import asyncio
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_google_adk import (
    ChapConfirmationBridge, ChapBridgeError, Decision, apply_reviewed_args,
)


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


def test_bridge_joins_the_agent_and_the_reviewer_when_it_records(coord, bridge):
    ws = coord.workspaces["wsp_adk_test"]
    assert "agent:assistant#v1" in ws.members
    # A person who decides at a desk joins there, with their own key, so the
    # bridge joins the reviewer only to record a decision in their name.
    assert "human:alice@example.org" not in ws.members
    bridge.record_decision(Call("transfer", {"amount": 100}), Conf(True))
    assert ws.members["human:alice@example.org"].type == "human"


def test_the_agent_cannot_review_its_own_calls(coord):
    with pytest.raises(ValueError, match="cannot be the agent"):
        ChapConfirmationBridge(coord, workspace="wsp_adk_self",
                               agent="human:alice@example.org",
                               reviewer="human:alice@example.org")


# ---- approve / reject derived from `confirmed` ---------------------


def test_confirmed_is_approve(bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    bridge.record_decision(Call("transfer", {"amount": 100}), Conf(True))
    assert seen == ["task.create", "task.complete", "review.request", "decide.approve"]


def test_not_confirmed_is_reject(bridge):
    bridge.record_decision(Call("transfer", {"amount": 100}), Conf(False))
    assert methods(bridge)[-1] == "decide.reject"


def test_a_decision_the_confirmation_does_not_carry_is_refused(bridge):
    # confirmed=True runs the tool, so it cannot be recorded as a reject; and
    # the other way round.
    with pytest.raises(ValueError, match="cannot be recorded as 'reject'"):
        bridge.record_decision(Call("transfer", {"amount": 100}), Conf(True),
                               decision="reject", rationale="not this recipient")
    with pytest.raises(ValueError, match="cannot be recorded as 'approve'"):
        bridge.record_decision(Call("transfer", {"amount": 100}), Conf(False),
                               decision="approve")
    assert methods(bridge) == ["workspace.create", "participant.join"]


# ---- an edit is what the confirmation carries ------------------------


def test_confirmed_with_a_tool_defined_payload_is_an_approve(bridge):
    # A payload without `args` is the tool's own data, and edits nothing.
    bridge.record_decision(Call("book_leave", {"days": 5}),
                           Conf(True, payload={"approved_days": 3}))
    assert methods(bridge)[-1] == "decide.approve"


def test_the_payload_args_are_the_override(bridge):
    bridge.record_decision(
        Call("transfer", {"amount": 100, "to": "acct-9"}),
        Conf(True, payload={"args": {"amount": 50, "to": "acct-9"}}),
        rationale="capped", tags=["limit"],
    )
    params = last_params(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "/args/amount", "value": 50}]
    assert params["intent_preserved"] is True
    assert params["rationale"] == "capped"


def test_an_override_the_confirmation_does_not_carry_is_refused(bridge):
    """The 0.3.4 example recorded edits passed as `returned` and resumed ADK
    with {"confirmed": True} alone, so the tool ran the original arguments.
    Such an override is now refused, and nothing is recorded."""
    with pytest.raises(ValueError, match="only when ADK will run it"):
        bridge.record_decision(
            Call("transfer", {"amount": 100, "to": "Alice"}), Conf(True),
            decision="override", returned={"amount": 50, "to": "Alice"})
    assert methods(bridge) == ["workspace.create", "participant.join"]


def test_returned_must_match_the_payload(bridge):
    with pytest.raises(ValueError, match="`returned` differs"):
        bridge.record_decision(
            Call("transfer", {"amount": 100}), Conf(True, payload={"args": {"amount": 50}}),
            decision="override", returned={"amount": 60})


def test_a_diff_must_give_the_payload_args(bridge):
    with pytest.raises(ValueError, match="`diff` does not give"):
        bridge.record_decision(
            Call("transfer", {"amount": 100}), Conf(True, payload={"args": {"amount": 50}}),
            diff=[{"op": "replace", "path": "/args/amount", "value": 60}])
    bridge.record_decision(
        Call("transfer", {"amount": 100}), Conf(True, payload={"args": {"amount": 50}}),
        diff=[{"op": "replace", "path": "/args/amount", "value": 50}])
    assert methods(bridge)[-1] == "decide.override"


def test_override_intent_preserved_false(bridge):
    bridge.record_decision(
        Call("transfer", {"to": "acct-9"}), Conf(True, payload={"args": {"to": "acct-escrow"}}),
        decision="override", intent_preserved=False,
    )
    assert last_params(bridge, "decide.override")["intent_preserved"] is False


def test_override_with_no_change_is_an_approve(bridge):
    bridge.record_decision(
        Call("transfer", {"amount": 100}), Conf(True, payload={"args": {"amount": 100}}),
        decision="override",
    )
    assert methods(bridge)[-1] == "decide.approve"


def test_apply_reviewed_args_runs_the_call_with_the_payload_args():
    class Ctx:
        def __init__(self, confirmation):
            self.tool_confirmation = confirmation

    args = {"amount": 100, "to": "Alice"}
    apply_reviewed_args(None, args, Ctx(Conf(True, payload={"args": {"amount": 50, "to": "Alice"}})))
    assert args == {"amount": 50, "to": "Alice"}
    for ctx in (Ctx(None), Ctx(Conf(True)), Ctx(Conf(True, payload={"approved_days": 3})),
                Ctx(Conf(False, payload={"args": {"amount": 1}}))):
        args = {"amount": 100}
        apply_reviewed_args(None, args, ctx)
        assert args == {"amount": 100}


# ---- structural reads ----------------------------------------------


# ---- waiting for a decision made at a desk ---------------------------


def desk_decides(coord, method, task_id, **params):
    coord.dispatch({"jsonrpc": "2.0", "id": "join", "method": "participant.join",
                    "params": {"workspace": "wsp_adk_test", "from": "human:alice@example.org",
                               "type": "human"}})
    out = coord.dispatch({"jsonrpc": "2.0", "id": "desk", "method": method,
                          "params": {"workspace": "wsp_adk_test",
                                     "from": "human:alice@example.org",
                                     "task_id": task_id, **params}})
    assert "error" not in out, out


def test_wait_for_decision_yields_to_the_desk(coord, bridge):
    artefact = {"tool": "transfer", "args": {"amount": 100}, "tool_call_id": "fc-1"}
    task_id = bridge._submit(artefact, bridge.reviewer, review_required=True)
    assert coord.workspaces["wsp_adk_test"].tasks[task_id].review_required is True

    async def desk():
        await asyncio.sleep(0.05)
        desk_decides(coord, "decide.override", task_id,
                     diff=[{"op": "replace", "path": "/args/amount", "value": 50}],
                     rationale="capped")

    async def both():
        waiting = asyncio.ensure_future(
            bridge.wait_for_decision(task_id, timeout=5, poll_seconds=0.01))
        await desk()
        return await waiting

    assert asyncio.run(both()) == Decision(
        task_id=task_id, state="completed", kind="override",
        reviewer="human:alice@example.org", comment="capped",
        output={**artefact, "args": {"amount": 50}})


def test_wait_for_decision_reads_a_client_with_task(coord):
    class DeskClient:
        def task(self, task_id):
            return {"task_id": task_id, "state": "declined", "output": None,
                    "review": {"decisions": [{"reviewer": "human:alice@example.org",
                                              "kind": "reject", "comment": "no"}]}}

    bridge = ChapConfirmationBridge(coord, workspace="wsp_adk_test", agent="agent:assistant#v1",
                                    reviewer="human:alice@example.org", client=DeskClient())
    decision = asyncio.run(bridge.wait_for_decision("tsk_remote", timeout=1))
    assert (decision.approved, decision.comment) == (False, "no")


def test_wait_for_decision_times_out(bridge):
    task_id = bridge._submit({"tool": "t", "args": {}, "tool_call_id": "fc-1"},
                             bridge.reviewer, review_required=True)
    with pytest.raises(TimeoutError):
        asyncio.run(bridge.wait_for_decision(task_id, timeout=0.05, poll_seconds=0.01))


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


def test_participant_type_rejects_unknown_scheme() -> None:
    from chap_google_adk import _participant_type
    assert _participant_type("human:alice") == "human"
    assert _participant_type("agent:bot#v1") == "agent"
    with pytest.raises(ValueError, match="known scheme"):
        _participant_type("alice")
