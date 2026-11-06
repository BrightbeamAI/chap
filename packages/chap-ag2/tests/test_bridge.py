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
import threading
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_ag2 import ChapTurnBridge, ChapBridgeError, Decision, ReviewRejected


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


def test_bridge_joins_the_agent_and_the_reviewer_when_it_records(coord, bridge):
    ws = coord.workspaces["wsp_ag2_test"]
    assert "agent:assistant#v1" in ws.members
    # A person who decides at a desk joins there, with their own key, so the
    # bridge joins the reviewer only to record a decision in their name.
    assert "human:alice@example.org" not in ws.members
    bridge.record_turn(msg("draft"), "")
    assert ws.members["human:alice@example.org"].type == "human"


def test_the_agent_cannot_review_its_own_messages(coord):
    with pytest.raises(ValueError, match="cannot be the agent"):
        ChapTurnBridge(coord, workspace="wsp_ag2_self",
                       agent="human:alice@example.org", reviewer="human:alice@example.org")


# ---- the safe fallback: only empty -> approve ----------------------


def test_empty_reply_infers_approve(bridge):
    tid = bridge.record_turn(msg("draft"), "")
    assert tid is not None
    assert methods(bridge)[-1] == "decide.approve"


def test_non_empty_reply_without_decision_records_nothing(bridge):
    tid = bridge.record_turn(msg("draft"), "what about the tax?")
    assert tid is None
    assert methods(bridge) == ["workspace.create", "participant.join"]


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


# ---- waiting for a decision made at a desk ---------------------------


def desk_decides(coord, method, **params):
    """A person at a desk sharing the coordinator: they join, and decide
    the review that is open."""
    coord.dispatch({"jsonrpc": "2.0", "id": "join", "method": "participant.join",
                    "params": {"workspace": "wsp_ag2_test", "from": "human:alice@example.org",
                               "type": "human"}})
    for _ in range(500):
        open_ = [t for t in coord.workspaces["wsp_ag2_test"].tasks.values()
                 if t.state == "review_requested"]
        if open_:
            out = coord.dispatch({"jsonrpc": "2.0", "id": "desk", "method": method,
                                  "params": {"workspace": "wsp_ag2_test",
                                             "from": "human:alice@example.org",
                                             "task_id": open_[0].id, **params}})
            assert "error" not in out, out
            return
        threading.Event().wait(0.01)
    raise AssertionError("no review reached the desk")


def reply_after(bridge, coord, method, **params):
    desk = threading.Thread(target=desk_decides, args=(coord, method), kwargs=params)
    desk.start()
    try:
        return bridge.request_reply(msg("refund $100 to Alice"), timeout=5, poll_seconds=0.01)
    finally:
        desk.join()


def test_request_reply_opens_a_review_the_task_requires(coord, bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    assert reply_after(bridge, coord, "decide.approve") == ""
    assert seen == ["task.create", "review.request"]
    (task,) = coord.workspaces["wsp_ag2_test"].tasks.values()
    assert (task.review_required, task.state, task.output) == (
        True, "completed", "refund $100 to Alice")


def test_request_reply_returns_the_reviewers_version(coord, bridge):
    reply = reply_after(bridge, coord, "decide.override",
                        diff=[{"op": "replace", "path": "", "value": "refund $50 to Alice"}],
                        rationale="over the limit")
    assert reply == "refund $50 to Alice"


def test_request_reply_returns_the_note_when_a_revision_is_asked_for(coord, bridge):
    reply = reply_after(bridge, coord, "decide.reject",
                        comment="ask for the order number first", request_revision=True)
    assert reply == "ask for the order number first"


def test_request_reply_raises_on_a_rejection(coord, bridge):
    with pytest.raises(ReviewRejected) as raised:
        reply_after(bridge, coord, "decide.reject", comment="not on the allow-list")
    assert (raised.value.state, raised.value.rationale) == ("declined", "not on the allow-list")


def test_wait_for_decision_reads_a_client_with_task(coord):
    class DeskClient:
        def task(self, task_id):
            return {"task_id": task_id, "state": "completed", "output": "edited",
                    "review": {"decisions": [{"reviewer": "human:alice@example.org",
                                              "kind": "override", "rationale": "shorter"}]}}

    bridge = ChapTurnBridge(coord, workspace="wsp_ag2_test", agent="agent:assistant#v1",
                            reviewer="human:alice@example.org", client=DeskClient())
    assert bridge.wait_for_decision("tsk_remote", timeout=1) == Decision(
        task_id="tsk_remote", state="completed", kind="override",
        reviewer="human:alice@example.org", output="edited", comment="shorter")


def test_wait_for_decision_times_out(bridge):
    with pytest.raises(TimeoutError):
        bridge.request_reply(msg("draft"), timeout=0.05, poll_seconds=0.01)


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


def test_participant_type_rejects_unknown_scheme() -> None:
    from chap_ag2 import _participant_type
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

    bridge = ChapTurnBridge(Recording(), workspace="wsp_ag2_ids", agent="agent:bot#v1",
                   reviewer="human:alice@example.org")
    bridge.audit()
    bridge.audit()
    assert len(ids) == len(set(ids)) == 4
