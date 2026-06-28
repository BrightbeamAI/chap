"""
Tests for chap-pydantic-ai. They run without Pydantic AI installed:
the bridge reads the resolution objects structurally, so small stand-ins
with the same attributes drive the same code path the real types would.

    1. Agent proposes a tool call -> task.create + complete + review.request.
    2. Human resolves it -> decide.approve | decide.override | decide.reject.
    3. The audit chain carries the whole trail, hash-linked.
"""

import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_pydantic_ai import ChapApprovalBridge, ChapBridgeError


# ---- Pydantic AI stand-ins -----------------------------------------


class Call:
    """A ToolCallPart with args already a dict (args_as_dict present)."""

    def __init__(self, tool_name, args, tool_call_id):
        self.tool_name = tool_name
        self._args = args
        self.tool_call_id = tool_call_id

    def args_as_dict(self):
        return dict(self._args)


class RawCall:
    """A ToolCallPart whose args arrived as a JSON string and which has no
    args_as_dict() -- exercises the coercion fallback."""

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


# ---- fixtures ------------------------------------------------------


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, enable_chain=True,
    ))


@pytest.fixture
def bridge(coord):
    return ChapApprovalBridge(
        coord,
        workspace="wsp_pai_test",
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
    ws = coord.workspaces["wsp_pai_test"]
    assert "agent:assistant#v1" in ws.members
    assert "human:alice@example.org" in ws.members


def test_bridge_is_idempotent(coord, bridge):
    ChapApprovalBridge(
        coord, workspace="wsp_pai_test",
        agent="agent:assistant#v1", reviewer="human:alice@example.org",
    )
    assert len(coord.workspaces["wsp_pai_test"].members) == 2


# ---- the three decisions -------------------------------------------


def test_approve(bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    bridge.record_decision(Call("send_email", {"to": "x@y.z"}, "c1"), ToolApproved())

    assert seen == ["task.create", "task.complete", "review.request", "decide.approve"]
    assert methods(bridge)[-1] == "decide.approve"


def test_approve_via_bool(bridge):
    bridge.record_decision(Call("send_email", {"to": "x@y.z"}, "c1"), True)
    assert methods(bridge)[-1] == "decide.approve"


def test_deny_records_message_as_comment(bridge):
    bridge.record_decision(Call("delete_file", {"path": "/etc/x"}, "c1"),
                           ToolDenied("deleting files is not allowed"))
    params = last_params(bridge, "decide.reject")
    assert params["comment"] == "deleting files is not allowed"


def test_deny_via_bool_uses_default_message(bridge):
    bridge.record_decision(Call("delete_file", {"path": "/etc/x"}, "c1"), False)
    assert last_params(bridge, "decide.reject")["comment"]


def test_override_diffs_only_the_changed_field(bridge):
    bridge.record_decision(
        Call("transfer", {"amount": 100, "to": "acct-9"}, "c1"),
        ToolApproved(override_args={"amount": 50, "to": "acct-9"}),
        rationale="cap to 50", tags=["limit"],
    )
    params = last_params(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "/args/amount", "value": 50}]
    assert params["rationale"] == "cap to 50"
    assert params["tags"] == ["limit"]


def test_override_intent_preserved_defaults_true(bridge):
    bridge.record_decision(
        Call("transfer", {"amount": 100}, "c1"),
        ToolApproved(override_args={"amount": 50}),
    )
    assert last_params(bridge, "decide.override")["intent_preserved"] is True


def test_override_with_no_actual_change_is_an_approve(bridge):
    # override_args identical to the original is not an edit; recording it
    # as an override would be a phantom in the "overrides as data" view.
    bridge.record_decision(
        Call("transfer", {"amount": 100}, "c1"),
        ToolApproved(override_args={"amount": 100}),
    )
    assert methods(bridge)[-1] == "decide.approve"


def test_override_intent_preserved_can_be_overridden(bridge):
    bridge.record_decision(
        Call("transfer", {"amount": 100}, "c1"),
        ToolApproved(override_args={"amount": 50}),
        intent_preserved=False,
    )
    assert last_params(bridge, "decide.override")["intent_preserved"] is False


# ---- identity ------------------------------------------------------


def test_per_decision_approver_is_joined_and_recorded(coord, bridge):
    bridge.record_decision(
        Call("ship", {"env": "prod"}, "c1"), ToolApproved(),
        approver="human:bob@example.org",
    )
    assert "human:bob@example.org" in coord.workspaces["wsp_pai_test"].members
    assert last_params(bridge, "decide.approve")["from"] == "human:bob@example.org"


def test_member_type_follows_the_uri_scheme(coord, bridge):
    bridge.record_decision(
        Call("ship", {"env": "prod"}, "c1"), ToolApproved(),
        approver="service:autodeploy",
    )
    ws = coord.workspaces["wsp_pai_test"]
    assert ws.members["service:autodeploy"].type == "service"
    assert ws.members["agent:assistant#v1"].type == "agent"
    assert ws.members["human:alice@example.org"].type == "human"


# ---- args coercion -------------------------------------------------


def test_stringified_args_are_normalised(bridge):
    bridge.record_decision(RawCall("send_email", '{"to": "x@y.z"}', "c1"), ToolApproved())
    artefact = last_params(bridge, "review.request")["artefact"]
    assert artefact["args"] == {"to": "x@y.z"}


# ---- record_results ------------------------------------------------


def test_record_results_raises_on_unresolved_approval(bridge):
    requests = Requests([Call("send_email", {"to": "x@y.z"}, "c1")])
    results = Results(approvals={})  # caller forgot to resolve c1
    with pytest.raises(ValueError, match="no resolution"):
        bridge.record_results(requests, results)


def test_record_results_walks_requests_and_metadata(bridge):
    requests = Requests([
        Call("send_email", {"to": "x@y.z"}, "c1"),
        Call("transfer", {"amount": 100}, "c2"),
    ])
    results = Results(
        approvals={"c1": ToolApproved(), "c2": ToolApproved(override_args={"amount": 50})},
        metadata={"c2": {"approver": "human:bob@example.org",
                         "rationale": "cap to 50", "tags": ["limit"]}},
    )
    bridge.record_results(requests, results)

    ms = methods(bridge)
    assert ms[-1] == "decide.override"
    assert "decide.approve" in ms
    ov = last_params(bridge, "decide.override")
    assert ov["from"] == "human:bob@example.org"
    assert ov["tags"] == ["limit"]


# ---- chain ---------------------------------------------------------


def test_chain_is_hash_linked(bridge):
    bridge.record_decision(Call("send_email", {"to": "x@y.z"}, "c1"), ToolApproved())
    bridge.record_decision(Call("rm", {"path": "/x"}, "c2"), ToolDenied("no"))
    audit = bridge.audit()
    assert len(audit) >= 8
    assert all("prev_hash" in e for e in audit)


# ---- errors --------------------------------------------------------


def test_coordinator_error_surfaces(bridge):
    # A decision on a task that was never opened is rejected by the
    # Coordinator and reaches the caller as a ChapBridgeError.
    with pytest.raises(ChapBridgeError):
        bridge._dispatch("decide.approve", {
            "workspace": bridge.workspace,
            "from": bridge.reviewer,
            "task_id": "tsk_does_not_exist",
        })
