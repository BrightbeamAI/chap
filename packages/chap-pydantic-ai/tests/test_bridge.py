"""
Tests for chap-pydantic-ai that run without Pydantic AI: recording a
decision made in your own code, which reads the resolution objects by their
attributes, so small stand-ins drive the path the real types would; and the
bridge made against a desk. tests/test_agent.py runs request_results with a
real Pydantic AI agent.
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
from chap_pydantic_ai import ChapApprovalBridge, ChapBridgeError, ChapError, PersonRequired

WS = "wsp_pai_test"
AGENT = "agent:assistant#v1"
REVIEWER = "human:alice@example.org"


class Call:
    """A ToolCallPart with args already a dict (args_as_dict present)."""

    def __init__(self, tool_name, args, tool_call_id):
        self.tool_name = tool_name
        self._args = args
        self.tool_call_id = tool_call_id

    def args_as_dict(self):
        return dict(self._args)


class RawCall:
    """A ToolCallPart whose args arrived as a JSON string, with no args_as_dict()."""

    def __init__(self, tool_name, args_json, tool_call_id):
        self.tool_name = tool_name
        self.args = args_json
        self.tool_call_id = tool_call_id


class ToolApproved:
    def __init__(self, override_args=None):
        self.override_args = override_args


class ToolDenied:
    def __init__(self, message="The tool call was denied."):
        self.message = message


class Requests:
    def __init__(self, approvals):
        self.approvals = approvals


class Results:
    def __init__(self, approvals, metadata=None):
        self.approvals = approvals
        self.metadata = metadata or {}


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True, enable_chain=True))


@pytest.fixture
def bridge(coord):
    return ChapApprovalBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)


def calls(bridge, method):
    return [e["envelope"]["params"] for e in bridge.audit()
            if "envelope" in e and e["envelope"]["method"] == method]


# ---- setup ---------------------------------------------------------


def test_the_bridge_joins_the_agent_and_nobody_else(coord, bridge):
    assert set(coord.workspaces[WS].members) == {AGENT}
    ChapApprovalBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    assert set(coord.workspaces[WS].members) == {AGENT}


def test_the_reviewer_is_a_person_and_not_the_agent(coord):
    with pytest.raises(ValueError, match="'agent:' or 'service:'"):
        ChapApprovalBridge(coord, workspace=WS, agent=REVIEWER, reviewer=REVIEWER)
    with pytest.raises(ValueError, match="'human:'"):
        ChapApprovalBridge(coord, workspace=WS, agent=AGENT, reviewer="agent:checker")


# ---- recording a decision made in your own code --------------------


def test_an_approval_is_recorded_as_recorded_and_bound_to_the_call(coord, bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    task_id = bridge.record_decision(Call("send_email", {"to": "x@y.z"}, "c1"), ToolApproved())
    assert [m for m in seen if m.startswith(("task.", "review.", "decide."))] == [
        "task.create", "task.complete", "decide.approve"]
    [params] = calls(bridge, "decide.approve")
    artefact = {"tool": "send_email", "args": {"to": "x@y.z"}, "tool_call_id": "c1"}
    assert (params["from"], params["via"], params["approved_artefact_digest"]) == (
        REVIEWER, "bridge-record", content_hash(artefact))
    task = coord.workspaces[WS].tasks[task_id]
    assert (task.state, task.review_required, task.output) == ("completed", True, artefact)


def test_an_approval_by_bool(bridge):
    bridge.record_decision(Call("send_email", {"to": "x@y.z"}, "c1"), True)
    assert len(calls(bridge, "decide.approve")) == 1


def test_a_denial_records_its_message(bridge):
    bridge.record_decision(Call("delete_file", {"path": "/etc/x"}, "c1"), ToolDenied("not allowed"))
    bridge.record_decision(Call("delete_file", {"path": "/etc/y"}, "c2"), False)
    assert [p["comment"] for p in calls(bridge, "decide.reject")] == ["not allowed", "The tool call was denied."]
    assert {p["via"] for p in calls(bridge, "decide.reject")} == {"bridge-record"}


def test_an_override_diffs_only_the_changed_argument(bridge):
    bridge.record_decision(Call("transfer", {"amount": 100, "to": "acct-9"}, "c1"),
                           ToolApproved(override_args={"amount": 50, "to": "acct-9"}),
                           rationale="cap to 50", tags=["limit"])
    [params] = calls(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "/args/amount", "value": 50}]
    assert (params["rationale"], params["tags"], params["intent_preserved"], params["via"]) == (
        "cap to 50", ["limit"], True, "bridge-record")
    assert params["approved_artefact_digest"] == content_hash(
        {"tool": "transfer", "args": {"amount": 100, "to": "acct-9"}, "tool_call_id": "c1"})


def test_an_override_that_changes_nothing_is_an_approval(bridge):
    bridge.record_decision(Call("transfer", {"amount": 100}, "c1"), ToolApproved(override_args={"amount": 100}))
    assert (len(calls(bridge, "decide.approve")), calls(bridge, "decide.override")) == (1, [])


def test_intent_preserved_can_be_said(bridge):
    bridge.record_decision(Call("transfer", {"amount": 100}, "c1"), ToolApproved(override_args={"amount": 50}),
                           intent_preserved=False)
    assert calls(bridge, "decide.override")[0]["intent_preserved"] is False


def test_a_decision_is_recorded_in_the_approvers_name(coord, bridge):
    bridge.record_decision(Call("ship", {"env": "prod"}, "c1"), ToolApproved(), approver="human:bob@example.org")
    assert coord.workspaces[WS].members["human:bob@example.org"].type == "human"
    assert calls(bridge, "decide.approve")[0]["from"] == "human:bob@example.org"
    with pytest.raises(ValueError, match="'human:' URI"):
        bridge.record_decision(Call("ship", {"env": "prod"}, "c2"), ToolApproved(), approver="service:autodeploy")


def test_stringified_args_are_read_as_an_object(bridge):
    bridge.record_decision(RawCall("send_email", '{"to": "x@y.z"}', "c1"), ToolApproved())
    assert calls(bridge, "task.complete")[0]["output"]["args"] == {"to": "x@y.z"}


def test_record_results_walks_the_requests_and_their_metadata(bridge):
    requests = Requests([Call("send_email", {"to": "x@y.z"}, "c1"), Call("transfer", {"amount": 100}, "c2")])
    with pytest.raises(ValueError, match="no resolution"):
        bridge.record_results(requests, Results(approvals={"c1": ToolApproved()}))
    results = Results(approvals={"c1": ToolApproved(), "c2": ToolApproved(override_args={"amount": 50})},
                      metadata={"c2": {"approver": "human:bob@example.org", "rationale": "cap", "tags": ["limit"]}})
    assert len(bridge.record_results(requests, results)) == 2
    [override] = calls(bridge, "decide.override")
    assert (override["from"], override["tags"]) == ("human:bob@example.org", ["limit"])


def test_nothing_is_recorded_in_a_persons_name_where_people_sign():
    coord = Coordinator(CoordinatorOptions(enable_chain=True, require_signatures=True, signature_policy="humans"))
    bridge = ChapApprovalBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    with pytest.raises(PersonRequired):
        bridge.record_decision(Call("transfer", {"amount": 100}, "c1"), ToolApproved())
    assert set(coord.workspaces[WS].members) == {AGENT}
    assert coord.workspaces[WS].tasks == {}


def test_the_chain_is_hash_linked_and_refusals_are_chap_errors(bridge):
    bridge.record_decision(Call("send_email", {"to": "x@y.z"}, "c1"), ToolApproved())
    bridge.record_decision(Call("rm", {"path": "/x"}, "c2"), ToolDenied("no"))
    assert all("prev_hash" in e for e in bridge.audit())
    assert ChapBridgeError is ChapError


def test_every_call_has_an_id_of_its_own(coord):
    ids = []

    class Recording:
        def dispatch(self, envelope):
            ids.append(envelope["id"])
            return coord.dispatch(envelope)

    bridge = ChapApprovalBridge(Recording(), workspace="wsp_pai_ids", agent=AGENT, reviewer=REVIEWER)
    bridge.audit()
    bridge.audit()
    assert len(ids) == len(set(ids)) == 4


# ---- against a desk where people sign -------------------------------


@pytest.fixture
def desk(tmp_path, monkeypatch):
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
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
        bridge = ChapApprovalBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
        with pytest.raises(PersonRequired):
            bridge.record_decision(Call("transfer", {"amount": 100}, "c1"), ToolApproved())
    # Alice's name is still hers: she joins from the desk with her own key.
    alice = Participant(desk, WS, REVIEWER, Signer.load_or_create(REVIEWER, tmp_path / "keys"))
    assert alice.join("human", "reviewer") == {"joined": True, "as": REVIEWER}
