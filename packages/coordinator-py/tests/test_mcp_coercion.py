"""
Regression tests for stringified-JSON argument coercion (Python).

Mirror of packages/coordinator-mcp/tests/coercion.test.ts. Reproduces
the real Claude Desktop failure where structured tool arguments arrived
as JSON-encoded strings, leaving the artefact stored as a string and
crashing a `/draft` object-path override.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.mcp_server import dispatch_tool_call
from chap_coordinator.transports.mcp_schemas import coerce_tool_args


def fresh_coord() -> Coordinator:
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=["core/1.0", "review/1.0", "routing/1.0"],
    ))


# ---- unit: coerce_tool_args --------------------------------------


def test_parses_stringified_object_for_output():
    out = coerce_tool_args("chap.task.complete", {
        "workspace": "wsp_demo", "from": "agent:bot@local",
        "task_id": "tsk_1", "output": '{"draft": "hello"}',
    })
    assert out["output"] == {"draft": "hello"}


def test_parses_stringified_array_for_review_to():
    out = coerce_tool_args("chap.review.request", {
        "to": '["human:me@local"]', "artefact": '{"draft": "hi"}',
    })
    assert out["to"] == ["human:me@local"]
    assert out["artefact"] == {"draft": "hi"}


def test_bare_uri_string_untouched():
    out = coerce_tool_args("chap.review.request", {"to": "human:me@local"})
    assert out["to"] == "human:me@local"


def test_ordinary_string_untouched():
    out = coerce_tool_args("chap.decide.override", {"rationale": "warmer phrasing"})
    assert out["rationale"] == "warmer phrasing"


def test_invalid_json_passed_through():
    out = coerce_tool_args("chap.task.complete", {"output": "{not valid json"})
    assert out["output"] == "{not valid json"


def test_parses_stringified_diff_array():
    out = coerce_tool_args("chap.decide.override", {
        "diff": '[{"op":"replace","path":"/draft","value":"x"}]',
    })
    assert out["diff"] == [{"op": "replace", "path": "/draft", "value": "x"}]


def test_does_not_mutate_input():
    inp = {"output": '{"a":1}'}
    out = coerce_tool_args("chap.task.complete", inp)
    assert inp["output"] == '{"a":1}'
    assert out["output"] == {"a": 1}


# ---- end-to-end: the Claude Desktop replay -----------------------


def test_object_path_override_succeeds_with_stringified_args():
    coord = fresh_coord()

    dispatch_tool_call(coord, "chap.workspace.create", {
        "workspace": "wsp_demo",
        "profiles": ["core/1.0", "review/1.0", "routing/1.0"],
    })
    dispatch_tool_call(coord, "chap.participant.join", {
        "workspace": "wsp_demo", "from": "human:me@local", "type": "human",
    })
    dispatch_tool_call(coord, "chap.participant.join", {
        "workspace": "wsp_demo", "from": "agent:bot@local", "type": "agent",
    })
    created = dispatch_tool_call(coord, "chap.task.create", {
        "workspace": "wsp_demo", "from": "human:me@local",
        "kind": "draft_response", "assignee": "agent:bot@local",
        "input": '{"channel":"email"}',                      # stringified
    })
    task_id = created["result"]["task_id"]

    dispatch_tool_call(coord, "chap.task.update", {
        "workspace": "wsp_demo", "from": "agent:bot@local",
        "task_id": task_id, "state": "in_progress",
    })
    dispatch_tool_call(coord, "chap.task.complete", {
        "workspace": "wsp_demo", "from": "agent:bot@local", "task_id": task_id,
        "output": '{"draft": "Your order is in transit; updates within 24 hours"}',  # stringified
        "confidence": "0.9",
    })
    dispatch_tool_call(coord, "chap.review.request", {
        "workspace": "wsp_demo", "from": "agent:bot@local", "task_id": task_id,
        "to": '["human:me@local"]',                                                  # stringified
        "artefact": '{"draft": "Your order is in transit; updates within 24 hours"}',  # stringified
        "rule": "any_one_approves",
    })

    override = dispatch_tool_call(coord, "chap.decide.override", {
        "workspace": "wsp_demo", "from": "human:me@local", "task_id": task_id,
        "diff": [{"op": "replace", "path": "/draft",
                  "value": "Your order is in transit; updates by tomorrow"}],
        "rationale": "warmer phrasing",
        "tags": ["tone-softened"],
        "intent_preserved": True,
    })

    assert "error" not in override, f"unexpected error: {override.get('error')}"
    assert override["result"]["state"] == "completed"
    assert override["result"]["applied"] == {
        "draft": "Your order is in transit; updates by tomorrow",
    }


def test_artefact_stored_as_object_in_audit_log():
    coord = fresh_coord()
    dispatch_tool_call(coord, "chap.workspace.create", {
        "workspace": "wsp_t", "profiles": ["core/1.0", "review/1.0"],
    })
    dispatch_tool_call(coord, "chap.participant.join", {
        "workspace": "wsp_t", "from": "human:me@local", "type": "human",
    })
    dispatch_tool_call(coord, "chap.participant.join", {
        "workspace": "wsp_t", "from": "agent:bot@local", "type": "agent",
    })
    created = dispatch_tool_call(coord, "chap.task.create", {
        "workspace": "wsp_t", "from": "human:me@local", "kind": "draft",
        "assignee": "agent:bot@local", "input": {"x": 1},
    })
    task_id = created["result"]["task_id"]
    dispatch_tool_call(coord, "chap.task.complete", {
        "workspace": "wsp_t", "from": "agent:bot@local", "task_id": task_id,
        "output": '{"draft": "hi"}',
    })

    audit = dispatch_tool_call(coord, "chap.audit.read", {
        "workspace": "wsp_t", "filter": {"method": "task.complete"},
    })
    entries = audit["result"]["entries"]
    assert len(entries) == 1
    assert entries[0]["envelope"]["params"]["output"] == {"draft": "hi"}
