"""
Tests for chap-google-adk that run without google-adk: recording a
confirmation your own code made, which reads calls, confirmations and
agents by their attributes, so plain values and small stand-ins drive the
path the real objects would; and the bridge made against a desk.
tests/test_runner.py runs both modes with a real ADK runner.
"""

import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions, content_hash
from chap_coordinator.client import HttpCoordinator, Participant, Signer
from chap_coordinator.server import load_config, make_coordinator, make_server
from chap_google_adk import (
    ChapBridgeError, ChapConfirmationBridge, ChapError, PersonRequired, apply_reviewed_args,
)

WS = "wsp_adk_test"
AGENT = "agent:assistant#v1"
REVIEWER = "human:alice@example.org"
ENROL = "TESTS-ENROL"
CALL = {"name": "transfer", "args": {"amount": 100, "to": "Alice"}, "id": "c1"}
ASKED = {"name": "adk_request_confirmation", "args": {
    "originalFunctionCall": CALL,
    "toolConfirmation": {"hint": "Please approve or reject.", "confirmed": False, "payload": None}}}
LEAVE = {"name": "adk_request_confirmation", "args": {
    "originalFunctionCall": {"name": "take_leave", "args": {"days": 5}, "id": "c2"},
    "toolConfirmation": {"hint": "How many days?", "confirmed": False, "payload": {"approved_days": 0}}}}


def transfer(amount: int, to: str, tool_context=None) -> str:
    return f"sent {amount} to {to}"


def take_leave(days: int, tool_context=None) -> str:
    return f"{days} days"


def confirmed(payload=None):
    return {"confirmed": True, "payload": payload}


EDITS_RUN = SimpleNamespace(canonical_before_tool_callbacks=[apply_reviewed_args], tools=[transfer, take_leave])
EDITS_IGNORED = SimpleNamespace(canonical_before_tool_callbacks=[], tools=[transfer])


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True, enable_chain=True))


@pytest.fixture
def bridge(coord):
    return ChapConfirmationBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)


def calls(bridge, method):
    return [e["envelope"]["params"] for e in bridge.audit()
            if "envelope" in e and e["envelope"]["method"] == method]


def test_the_bridge_joins_the_agent_and_nobody_else(coord, bridge):
    assert set(coord.workspaces[WS].members) == {AGENT}


# ---- what a confirmation records -------------------------------------------


def test_confirmed_is_an_approval_marked_as_recorded_and_bound_to_the_call(coord, bridge):
    task_id = bridge.record_decision(ASKED, confirmed())
    [params] = calls(bridge, "decide.approve")
    artefact = {"tool": "transfer", "args": {"amount": 100, "to": "Alice"}, "tool_call_id": "c1"}
    assert (params["from"], params["via"], params["approved_artefact_digest"]) == (
        REVIEWER, "bridge-record", content_hash(artefact))
    assert coord.workspaces[WS].tasks[task_id].output == artefact


def test_not_confirmed_is_a_rejection(bridge):
    bridge.record_decision(CALL, {"confirmed": False}, rationale="not this month")
    assert calls(bridge, "decide.reject")[0]["comment"] == "not this month"


def test_an_edit_of_the_arguments_is_recorded_where_adk_runs_it(coord, bridge):
    task_id = bridge.record_decision(ASKED, confirmed({"args": {"amount": 50, "to": "Alice"}}),
                                     llm_agent=EDITS_RUN, rationale="over the limit")
    [params] = calls(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "/args/amount", "value": 50}]
    assert (params["rationale"], params["intent_preserved"], params["via"]) == ("over the limit", True, "bridge-record")
    assert coord.workspaces[WS].tasks[task_id].output["args"] == {"amount": 50, "to": "Alice"}


@pytest.mark.parametrize("agent, args, why", [
    (None, {"amount": 50, "to": "Alice"}, "apply_reviewed_args"),          # nothing shows the edit runs
    (EDITS_IGNORED, {"amount": 50, "to": "Alice"}, "apply_reviewed_args"),   # the agent runs the model's
    (EDITS_RUN, {"amount": 50}, "transfer needs to"),                         # ADK refuses the call
    (EDITS_RUN, {"amount": 60, "to": "Alice", "memo": "x"}, "takes no memo"),  # ADK drops memo
])
def test_an_edit_adk_would_not_run_as_recorded_is_refused(coord, bridge, agent, args, why):
    with pytest.raises(ValueError, match=why):
        bridge.record_decision(ASKED, confirmed({"args": args}), llm_agent=agent)
    assert coord.workspaces[WS].tasks == {}


def test_a_tools_own_payload_is_recorded_as_the_tool_reads_it(coord, bridge):
    task_id = bridge.record_decision(LEAVE, confirmed({"approved_days": 2}))
    [params] = calls(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "/payload/approved_days", "value": 2}]
    output = coord.workspaces[WS].tasks[task_id].output
    assert (output["args"], output["payload"], output["hint"]) == ({"days": 5}, {"approved_days": 2}, "How many days?")


def test_a_tools_own_payload_is_recorded_when_only_the_call_is_given(bridge):
    bridge.record_decision(LEAVE["args"]["originalFunctionCall"], confirmed({"approved_days": 2}))
    assert calls(bridge, "decide.override")[0]["diff"] == [
        {"op": "add", "path": "/payload", "value": {"approved_days": 2}}]


def test_the_payload_the_tool_asked_for_unchanged_is_an_approval(bridge):
    bridge.record_decision(LEAVE, confirmed({"approved_days": 0}))
    assert len(calls(bridge, "decide.approve")) == 1


def test_an_edit_that_changes_nothing_is_an_approval(bridge):
    bridge.record_decision(ASKED, confirmed({"args": dict(CALL["args"])}), decision="override")
    assert (len(calls(bridge, "decide.approve")), calls(bridge, "decide.override")) == (1, [])


@pytest.mark.parametrize("kwargs, message", [
    ({"decision": "reject"}, "makes this call 'approve'"),
    ({"decision": "override"}, None),
    ({"decision": "maybe"}, "decision must be"),
    ({"returned": {"amount": 1, "to": "Alice"}}, "`returned` differs"),
])
def test_a_restatement_must_agree_with_the_confirmation(bridge, kwargs, message):
    if message is None:
        bridge.record_decision(ASKED, confirmed(), **kwargs)
        return
    with pytest.raises(ValueError, match=message):
        bridge.record_decision(ASKED, confirmed(), **kwargs)


def test_a_diff_must_give_what_the_confirmation_runs(bridge):
    edited = confirmed({"args": {"amount": 50, "to": "Alice"}})
    with pytest.raises(ValueError, match="does not give"):
        bridge.record_decision(ASKED, edited, llm_agent=EDITS_RUN,
                               diff=[{"op": "replace", "path": "/args/amount", "value": 40}])
    bridge.record_decision(ASKED, edited, llm_agent=EDITS_RUN,
                           diff=[{"op": "replace", "path": "/args/amount", "value": 50}])
    assert len(calls(bridge, "decide.override")) == 1


def test_a_decision_is_recorded_in_the_approvers_name(bridge):
    bridge.record_decision(CALL, confirmed(), approver="human:bob@example.org")
    assert calls(bridge, "decide.approve")[0]["from"] == "human:bob@example.org"
    with pytest.raises(ValueError, match="'human:' URI"):
        bridge.record_decision(CALL, confirmed(), approver="service:autodeploy")


def test_apply_reviewed_args_runs_the_call_with_the_payload_args():
    args = {"amount": 100, "to": "Alice"}
    apply_reviewed_args(None, args, SimpleNamespace(tool_confirmation=SimpleNamespace(
        confirmed=True, payload={"args": {"amount": 50, "to": "Alice"}})))
    assert args == {"amount": 50, "to": "Alice"}
    for confirmation in (None, SimpleNamespace(confirmed=False, payload={"args": {"amount": 1}}),
                         SimpleNamespace(confirmed=True, payload={"approved_days": 2})):
        args = {"amount": 100}
        apply_reviewed_args(None, args, SimpleNamespace(tool_confirmation=confirmation))
        assert args == {"amount": 100}


def test_nothing_is_recorded_in_a_persons_name_where_people_sign():
    coord = Coordinator(CoordinatorOptions(enable_chain=True, require_signatures=True, signature_policy="humans"))
    bridge = ChapConfirmationBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    with pytest.raises(PersonRequired):
        bridge.record_decision(ASKED, confirmed())
    assert set(coord.workspaces[WS].members) == {AGENT} and coord.workspaces[WS].tasks == {}


def test_refusals_are_chap_errors_and_every_call_has_an_id_of_its_own(coord):
    assert ChapBridgeError is ChapError
    ids = []

    class Recording:
        def dispatch(self, envelope):
            ids.append(envelope["id"])
            return coord.dispatch(envelope)

    bridge = ChapConfirmationBridge(Recording(), workspace="wsp_adk_ids", agent=AGENT, reviewer=REVIEWER)
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


def test_the_bridge_is_made_against_a_desk_where_people_sign_and_records_nothing_there(desk, tmp_path):
    pytest.importorskip("cryptography")
    for coord in (desk, Participant(desk, WS, AGENT)):
        bridge = ChapConfirmationBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
        with pytest.raises(PersonRequired):
            bridge.record_decision(ASKED, confirmed())
    alice = Participant(desk, WS, REVIEWER, Signer.load_or_create(REVIEWER, tmp_path / "keys"),
                        enrolment=ENROL)
    assert alice.join("human", "reviewer") == {"joined": True, "as": REVIEWER}
