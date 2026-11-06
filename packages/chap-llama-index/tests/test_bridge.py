"""
Tests for chap-llama-index. They run without llama-index-workflows: the
bridge reads events via event.get(...), so a plain dict stands in for an
InputRequiredEvent or HumanResponseEvent.

    1. A step proposes output -> task.create + complete + review.request.
    2. A human answers -> decide.approve | decide.override | decide.reject.
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
from chap_llama_index import ChapHitlBridge, ChapBridgeError, Decision


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


def test_bridge_joins_the_agent_and_the_reviewer_when_it_records(coord, bridge):
    ws = coord.workspaces["wsp_li_test"]
    assert "agent:writer#v1" in ws.members
    # A person who decides at a desk joins there, with their own key, so the
    # bridge joins the reviewer only to record a decision in their name.
    assert "human:alice@example.org" not in ws.members
    bridge.record_decision(ask({"reply": "hi"}), reply(response="ok"), decision="approve")
    assert ws.members["human:alice@example.org"].type == "human"


def test_bridge_is_idempotent(coord, bridge):
    ChapHitlBridge(coord, workspace="wsp_li_test",
                   agent="agent:writer#v1", reviewer="human:alice@example.org")
    assert len(coord.workspaces["wsp_li_test"].members) == 1


def test_the_agent_cannot_review_its_own_output(coord):
    with pytest.raises(ValueError, match="cannot be the agent"):
        ChapHitlBridge(coord, workspace="wsp_li_self",
                       agent="human:alice@example.org", reviewer="human:alice@example.org")


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


# ---- waiting for a decision made at a desk ---------------------------


def desk_decides(coord, method, task_id, **params):
    """A decision made outside the bridge, as a desk sharing the
    coordinator would make it: the person joins, then decides."""
    coord.dispatch({"jsonrpc": "2.0", "id": "join", "method": "participant.join",
                    "params": {"workspace": "wsp_li_test", "from": "human:alice@example.org",
                               "type": "human"}})
    out = coord.dispatch({"jsonrpc": "2.0", "id": "desk", "method": method,
                          "params": {"workspace": "wsp_li_test",
                                     "from": "human:alice@example.org",
                                     "task_id": task_id, **params}})
    assert "error" not in out, out


def test_the_review_waits_for_a_person(coord, bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    task_id = bridge._submit({"amount": 100}, bridge.reviewer, review_required=True)
    assert seen == ["task.create", "review.request"]
    task = coord.workspaces["wsp_li_test"].tasks[task_id]
    assert (task.state, task.review_required) == ("review_requested", True)
    assert "human:alice@example.org" not in coord.workspaces["wsp_li_test"].members


def test_wait_for_decision_yields_to_the_desk(coord, bridge):
    """The wait is a coroutine: a desk on the same event loop decides
    during the wait."""
    task_id = bridge._submit({"amount": 100}, bridge.reviewer, review_required=True)

    async def desk():
        await asyncio.sleep(0.05)
        desk_decides(coord, "decide.override", task_id,
                     diff=[{"op": "replace", "path": "/amount", "value": 50}],
                     rationale="capped", tags=["limit"])

    async def both():
        waiting = asyncio.ensure_future(
            bridge.wait_for_decision(task_id, timeout=5, poll_seconds=0.01))
        await desk()
        return await waiting

    assert asyncio.run(both()) == Decision(
        task_id=task_id, state="completed", kind="override",
        reviewer="human:alice@example.org", output={"amount": 50},
        comment="capped", tags=("limit",))


def test_wait_for_decision_reads_a_client_with_task(coord):
    class DeskClient:
        def task(self, task_id):
            return {"task_id": task_id, "state": "completed", "output": {"amount": 100},
                    "review": {"decisions": [{"reviewer": "human:alice@example.org",
                                              "kind": "approve", "comment": None}]}}

    bridge = ChapHitlBridge(coord, workspace="wsp_li_test", agent="agent:writer#v1",
                            reviewer="human:alice@example.org", client=DeskClient())
    decision = asyncio.run(bridge.wait_for_decision("tsk_remote", timeout=1))
    assert (decision.approved, decision.output) == (True, {"amount": 100})


def test_wait_for_decision_times_out(bridge):
    task_id = bridge._submit({"amount": 100}, bridge.reviewer, review_required=True)
    with pytest.raises(TimeoutError):
        asyncio.run(bridge.wait_for_decision(task_id, timeout=0.05, poll_seconds=0.01))


# ---- guards --------------------------------------------------------


def test_unknown_decision_raises(bridge):
    with pytest.raises(ValueError, match="approve"):
        bridge.record_decision(ask({"x": 1}), reply(response="?"), decision="maybe")


def test_override_without_returned_content_raises(bridge):
    with pytest.raises(ValueError, match="returned"):
        bridge.record_decision(ask({"x": 1}), reply(), decision="override")


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


def test_participant_type_rejects_unknown_scheme() -> None:
    from chap_llama_index import _participant_type
    assert _participant_type("human:alice") == "human"
    assert _participant_type("agent:bot#v1") == "agent"
    with pytest.raises(ValueError, match="known scheme"):
        _participant_type("alice")


def test_every_call_has_an_id_of_its_own(coord):
    """A client that signs what the bridge sends signed two identical calls
    alike under one id, and a coordinator takes a signed request once, so
    the second was refused as a replay. Each call now has an id of its own."""
    ids = []

    class Recording:
        workspaces = coord.workspaces

        def dispatch(self, envelope):
            ids.append(envelope["id"])
            return coord.dispatch(envelope)

    bridge = ChapHitlBridge(Recording(), workspace="wsp_li_ids", agent="agent:bot#v1",
                   reviewer="human:alice@example.org")
    bridge.audit()
    bridge.audit()
    assert len(ids) == len(set(ids)) == 4
