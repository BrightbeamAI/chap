"""
Tests for the inward wrap helpers (``transports/wrap.py``).
"""
from __future__ import annotations

import pytest

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.wrap import (
    content_hash,
    wrap_a2a_message_exchange,
    wrap_mcp_tool_call,
)


def _make_coord() -> Coordinator:
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True,
        deterministic_clock=True,
        default_profiles=["core/1.0", "review/1.0", "audit-scitt/1.0"],
    ))
    coord.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                    "params": {"workspace": "wsp_wrap"}})
    coord.dispatch({"jsonrpc": "2.0", "id": "2", "method": "participant.join",
                    "params": {"workspace": "wsp_wrap",
                               "from": "agent:bot", "type": "agent", "role": "drafter"}})
    coord.dispatch({"jsonrpc": "2.0", "id": "3", "method": "participant.join",
                    "params": {"workspace": "wsp_wrap",
                               "from": "service:a2a-bridge", "type": "service", "role": "bridge"}})
    return coord


# ============================================================
# content_hash
# ============================================================

def test_content_hash_format() -> None:
    h = content_hash({"a": 1, "b": [1, 2, 3]})
    assert h.startswith("sha256:")
    assert len(h) == 7 + 64


def test_content_hash_is_canonical() -> None:
    # JCS sorts object keys, so these should hash identically.
    h1 = content_hash({"a": 1, "b": 2})
    h2 = content_hash({"b": 2, "a": 1})
    assert h1 == h2


def test_content_hash_differs_on_content() -> None:
    assert content_hash({"a": 1}) != content_hash({"a": 2})


# ============================================================
# wrap_mcp_tool_call
# ============================================================

def test_wrap_mcp_emits_task_create_and_complete() -> None:
    coord = _make_coord()
    res = wrap_mcp_tool_call(
        coord, "wsp_wrap",
        caller="agent:bot",
        tool="github.create_issue",
        server="github",
        args={"title": "bug", "body": "details"},
        result={"issue_url": "https://github.com/example/repo/issues/1"},
        confidence=0.95,
    )
    assert res["task_id"].startswith("tsk_")
    assert res["input_hash"].startswith("sha256:")
    assert res["output_hash"].startswith("sha256:")

    ws = coord.get_workspace("wsp_wrap")
    assert ws is not None
    task = ws.tasks[res["task_id"]]
    assert task.state == "completed"
    assert task.kind == "mcp_call:github.create_issue"
    assert task.assignee == "agent:bot"
    assert task.delegator == "agent:bot"
    # output should carry the result and a citation
    assert task.output["result"]["issue_url"] == "https://github.com/example/repo/issues/1"
    citations = task.output["citations"]
    assert len(citations) == 1
    assert citations[0]["server"] == "github"
    assert citations[0]["tool"] == "github.create_issue"
    assert citations[0]["input_hash"] == res["input_hash"]
    assert citations[0]["output_hash"] == res["output_hash"]


def test_wrap_mcp_routing_hints_attach_to_task() -> None:
    coord = _make_coord()
    res = wrap_mcp_tool_call(
        coord, "wsp_wrap",
        caller="agent:bot", tool="some.tool",
        args={}, result={"ok": True},
        routing_hints={"criticality": "low", "risk_tier": "standard"},
    )
    ws = coord.get_workspace("wsp_wrap")
    task = ws.tasks[res["task_id"]]
    assert task.routing_hints["criticality"] == "low"
    assert task.routing_hints["risk_tier"] == "standard"


def test_wrap_mcp_validates_required_args() -> None:
    coord = _make_coord()
    with pytest.raises(ValueError, match="workspace"):
        wrap_mcp_tool_call(coord, "", caller="x", tool="y", args={}, result={})
    with pytest.raises(ValueError, match="caller"):
        wrap_mcp_tool_call(coord, "wsp_wrap", caller="", tool="y", args={}, result={})
    with pytest.raises(ValueError, match="tool"):
        wrap_mcp_tool_call(coord, "wsp_wrap", caller="agent:bot", tool="",
                           args={}, result={})


def test_wrap_mcp_chap_error_raises() -> None:
    coord = _make_coord()
    # Caller not in workspace -> task.create fails
    with pytest.raises(RuntimeError, match="task.create failed"):
        wrap_mcp_tool_call(
            coord, "wsp_wrap",
            caller="agent:not-joined", tool="t",
            args={}, result={},
        )


def test_wrap_mcp_lands_in_audit_log() -> None:
    coord = _make_coord()
    before = len(coord.get_workspace("wsp_wrap").audit)
    wrap_mcp_tool_call(
        coord, "wsp_wrap", caller="agent:bot",
        tool="t", args={}, result={"ok": True},
    )
    after = len(coord.get_workspace("wsp_wrap").audit)
    # task.create + task.update + task.complete = 3 envelopes
    assert after - before == 3


# ============================================================
# wrap_a2a_message_exchange
# ============================================================

def test_wrap_a2a_basic() -> None:
    coord = _make_coord()
    res = wrap_a2a_message_exchange(
        coord, "wsp_wrap",
        bridge_uri="service:a2a-bridge",
        remote_agent="a2a:partner-org/agent-1",
        sent={"task": "summarise", "doc": "hello world"},
        received={"summary": "Hello, world."},
        confidence=0.9,
    )
    assert res["task_id"].startswith("tsk_")
    ws = coord.get_workspace("wsp_wrap")
    task = ws.tasks[res["task_id"]]
    assert task.state == "completed"
    assert task.kind == "a2a_exchange"
    assert task.assignee == "service:a2a-bridge"
    assert task.input["remote_agent"] == "a2a:partner-org/agent-1"
    assert task.output["received"]["summary"] == "Hello, world."

    citation = task.output["citations"][0]
    assert citation["kind"] == "a2a_exchange"
    assert citation["remote_agent"] == "a2a:partner-org/agent-1"
    assert citation["sent_hash"] == res["sent_hash"]
    assert citation["received_hash"] == res["received_hash"]


def test_wrap_a2a_validates_required_args() -> None:
    coord = _make_coord()
    with pytest.raises(ValueError, match="workspace"):
        wrap_a2a_message_exchange(coord, "",
            bridge_uri="b", remote_agent="r", sent={}, received={})
    with pytest.raises(ValueError, match="bridge_uri"):
        wrap_a2a_message_exchange(coord, "wsp_wrap",
            bridge_uri="", remote_agent="r", sent={}, received={})
    with pytest.raises(ValueError, match="remote_agent"):
        wrap_a2a_message_exchange(coord, "wsp_wrap",
            bridge_uri="service:a2a-bridge", remote_agent="", sent={}, received={})
