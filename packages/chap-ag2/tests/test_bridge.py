"""
Tests for chap-ag2 that run without AG2: what a message puts in front of a
person, recording a turn your own code took, which reads the message and
reply as plain values, and the bridge made against a desk.
tests/test_conversation.py runs request_reply in an AG2 0.x chat, and
tests/test_tool_approval.py runs tool_approval with AG2 1.x.
"""

import json
import sys
import threading
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions, content_hash
from chap_coordinator.client import HttpCoordinator, Participant, Signer
from chap_coordinator.server import load_config, make_coordinator, make_server
from chap_ag2 import ChapBridgeError, ChapError, ChapTurnBridge, PersonRequired

WS = "wsp_ag2_test"
AGENT = "agent:assistant#v1"
REVIEWER = "human:alice@example.org"
ENROL = "TESTS-ENROL"


def msg(content, name="assistant"):
    return {"content": content, "role": "user", "name": name}


TOOL_CALL = {"content": None, "role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {
    "name": "transfer", "arguments": json.dumps({"amount": 10000, "to": "Mallory"})}}]}


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True, enable_chain=True))


@pytest.fixture
def bridge(coord):
    return ChapTurnBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)


def calls(bridge, method):
    return [e["envelope"]["params"] for e in bridge.audit()
            if "envelope" in e and e["envelope"]["method"] == method]


def test_the_bridge_joins_the_agent_and_nobody_else(coord, bridge):
    assert set(coord.workspaces[WS].members) == {AGENT}


# ---- what the person is shown -----------------------------------------


def test_a_tool_call_message_is_reviewed_with_its_calls(bridge):
    bridge.record_turn(TOOL_CALL, "")
    [completed] = calls(bridge, "task.complete")
    assert completed["output"] == {"content": None, "tool_calls": [
        {"id": "call_1", "name": "transfer", "arguments": {"amount": 10000, "to": "Mallory"}}]}


def test_a_function_call_message_is_reviewed_with_its_call(bridge):
    bridge.record_turn({"content": "paying", "function_call": {"name": "transfer", "arguments": "{\"amount\": 5}"}}, "")
    assert calls(bridge, "task.complete")[0]["output"] == {"content": "paying", "tool_calls": [
        {"id": None, "name": "transfer", "arguments": {"amount": 5}}]}


def test_a_text_message_is_reviewed_as_its_text(bridge):
    bridge.record_turn(msg("the draft body"), "", decision="approve")
    assert calls(bridge, "task.complete")[0]["output"] == "the draft body"


def test_a_message_with_nothing_in_it_is_not_put_in_front_of_anyone(coord, bridge):
    for empty in (msg(None), msg("  "), {"content": None, "tool_calls": []}):
        with pytest.raises(ValueError, match="nothing to review"):
            bridge.record_turn(empty, "", decision="approve")
    assert coord.workspaces[WS].tasks == {}


# ---- recording a turn your own code took ----------------------------------


def test_an_empty_reply_is_taken_for_an_approval_and_nothing_else_is(bridge):
    assert bridge.record_turn(msg("draft"), "") is not None
    assert bridge.record_turn(msg("draft"), "what about the tax?") is None
    assert bridge.record_turn(msg("draft"), "exit") is None
    assert len(calls(bridge, "decide.approve")) == 1 and calls(bridge, "decide.reject") == []


def test_a_recorded_approval_is_marked_and_bound_to_the_message(bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    bridge.record_turn(msg("draft"), "", decision="approve")
    assert [m for m in seen if m.startswith(("task.", "review.", "decide."))] == [
        "task.create", "task.complete", "decide.approve"]
    [params] = calls(bridge, "decide.approve")
    assert (params["from"], params["via"], params["approved_artefact_digest"]) == (
        REVIEWER, "bridge-record", content_hash("draft"))


def test_an_override_diffs_the_message_against_the_reply(bridge):
    bridge.record_turn(msg("refund $100 to Alice"), "refund $50 to Alice", decision="override",
                       rationale="over the limit", tags=["capped"])
    [params] = calls(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "", "value": "refund $50 to Alice"}]
    assert (params["rationale"], params["intent_preserved"], params["via"]) == (
        "over the limit", True, "bridge-record")
    bridge.record_turn(msg("ship it"), "hold the release", decision="override", intent_preserved=False)
    assert calls(bridge, "decide.override")[-1]["intent_preserved"] is False


def test_an_override_that_changes_nothing_is_an_approval(bridge):
    bridge.record_turn(msg("keep as is"), "keep as is", decision="override")
    assert (len(calls(bridge, "decide.approve")), calls(bridge, "decide.override")) == (1, [])


def test_an_edit_of_a_tool_call_is_not_recorded_since_ag2_would_not_run_it(coord, bridge):
    with pytest.raises(ValueError, match="never run"):
        bridge.record_turn(TOOL_CALL, "transfer 100 to Alice", decision="override")
    assert coord.workspaces[WS].tasks == {}


def test_a_rejection_takes_its_note_from_the_reply(bridge):
    bridge.record_turn(msg("rude draft"), "tone is unacceptable", decision="reject")
    assert calls(bridge, "decide.reject")[0]["comment"] == "tone is unacceptable"


def test_a_turn_is_recorded_in_the_approvers_name(coord, bridge):
    bridge.record_turn(msg("draft"), "", decision="approve", approver="human:bob@example.org")
    assert calls(bridge, "decide.approve")[0]["from"] == "human:bob@example.org"
    with pytest.raises(ValueError, match="'human:' URI"):
        bridge.record_turn(msg("draft"), "", decision="approve", approver="service:autodeploy")
    with pytest.raises(ValueError, match="decision must be"):
        bridge.record_turn(msg("draft"), "", decision="maybe")


def test_nothing_is_recorded_in_a_persons_name_where_people_sign():
    coord = Coordinator(CoordinatorOptions(enable_chain=True, require_signatures=True, signature_policy="humans"))
    bridge = ChapTurnBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    with pytest.raises(PersonRequired):
        bridge.record_turn(msg("draft"), "", decision="approve")
    assert set(coord.workspaces[WS].members) == {AGENT} and coord.workspaces[WS].tasks == {}


def test_refusals_are_chap_errors_and_every_call_has_an_id_of_its_own(coord):
    assert ChapBridgeError is ChapError
    ids = []

    class Recording:
        def dispatch(self, envelope):
            ids.append(envelope["id"])
            return coord.dispatch(envelope)

    bridge = ChapTurnBridge(Recording(), workspace="wsp_ag2_ids", agent=AGENT, reviewer=REVIEWER)
    bridge.audit()
    bridge.audit()
    assert len(ids) == len(set(ids)) == 4


@pytest.fixture
def desk(tmp_path, monkeypatch):
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    # A person's first join takes the code the desk server prints at start.
    monkeypatch.setenv("CHAP_ENROLMENT_CODE", ENROL)
    path = tmp_path / "chap.config.json"
    path.write_text(json.dumps({
        "workspace": WS, "profiles": ["core/1.0", "review/1.0"], "store": ":memory:", "port": 0,
        "require_signatures": "humans", "chain": True,
        "humans": [{"uri": REVIEWER, "display_name": "Alice", "role": "reviewer"}], "agent": {"uri": AGENT},
    }), encoding="utf-8")
    config = load_config(path)
    server = make_server(config, make_coordinator(config))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield HttpCoordinator(f"http://127.0.0.1:{server.server_port}/chap")
    finally:
        server.shutdown()
        server.server_close()


def test_request_reply_against_a_desk_where_people_sign(desk, tmp_path):
    pytest.importorskip("cryptography")
    alice = Participant(desk, WS, REVIEWER, Signer.load_or_create(REVIEWER, tmp_path / "keys"),
                        enrolment=ENROL)
    alice.join("human", "reviewer")
    for coord in (desk, Participant(desk, WS, AGENT)):
        bridge = ChapTurnBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)

        def decide():
            while not (reviews := desk.get("/api/reviews")["reviews"]):
                pass
            alice.call("decide.override", task_id=reviews[0]["task_id"], rationale="smaller",
                       diff=[{"op": "replace", "path": "", "value": "Draft: refund $50."}])

        person = threading.Thread(target=decide)
        person.start()
        assert bridge.request_reply(msg("Draft: refund $100."), timeout=5, poll_seconds=0.02) == "Draft: refund $50."
        person.join()
        with pytest.raises(PersonRequired):
            bridge.record_turn(msg("draft"), "", decision="approve")
