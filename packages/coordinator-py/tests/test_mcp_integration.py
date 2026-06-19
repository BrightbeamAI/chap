"""
Integration tests: an MCP client (using the official SDK) drives a
CHAP Coordinator through the MCP transport. End-to-end across the
JSON-RPC + MCP boundary.

Mirrors ``packages/coordinator-mcp/tests/integration.test.ts`` so the
two implementations stay aligned.
"""
from __future__ import annotations

import json

import pytest
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.mcp_server import make_chap_mcp_server
from chap_coordinator.transports.mcp_schemas import TOOL_NAMES


def _make_coord() -> Coordinator:
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True,
        deterministic_clock=True,
        default_profiles=[
            "core/1.0", "review/1.0", "whisper/1.0",
            "deliberation/1.0", "handoff/1.0", "control/1.0",
            "routing/1.0", "audit-scitt/1.0",
        ],
    ))


def _unwrap(result) -> object:
    """Pull the JSON body out of a CallToolResult's text content."""
    if result.isError:
        text = result.content[0].text if result.content else "(no content)"
        raise AssertionError(f"Tool call errored: {text}")
    block = result.content[0]
    return json.loads(block.text)


# ============================================================

@pytest.mark.asyncio
async def test_list_tools_returns_every_method() -> None:
    coord = _make_coord()
    server = make_chap_mcp_server(coord)
    async with create_connected_server_and_client_session(server) as client:
        result = await client.list_tools()
        assert len(result.tools) == len(TOOL_NAMES), \
            f"should expose all {len(TOOL_NAMES)} methods, got {len(result.tools)}"
        names = {t.name for t in result.tools}
        assert "chap.workspace.create" in names
        assert "chap.task.create"      in names
        assert "chap.decide.override"  in names
        assert "chap.deliberate.open"  in names


@pytest.mark.asyncio
async def test_workspace_create_through_mcp() -> None:
    coord = _make_coord()
    server = make_chap_mcp_server(coord)
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("chap.workspace.create", {"workspace": "wsp_mcp_test"})
        body = _unwrap(result)
        assert body["workspace"] == "wsp_mcp_test"
        assert coord.get_workspace("wsp_mcp_test") is not None


@pytest.mark.asyncio
async def test_chap_error_surfaces_as_tool_error() -> None:
    coord = _make_coord()
    server = make_chap_mcp_server(coord)
    async with create_connected_server_and_client_session(server) as client:
        result = await client.call_tool("chap.workspace.describe", {"workspace": "wsp_missing"})
        assert result.isError
        body = json.loads(result.content[0].text)
        assert body["chap_error"] == -32602


@pytest.mark.asyncio
async def test_full_workflow_through_mcp() -> None:
    coord = _make_coord()
    server = make_chap_mcp_server(coord)
    async with create_connected_server_and_client_session(server) as client:

        _unwrap(await client.call_tool("chap.workspace.create",
            {"workspace": "wsp_flow"}))

        for from_uri, ptype, role in (
            ("human:alice", "human", "owner"),
            ("human:bob",   "human", "reviewer"),
            ("agent:bot",   "agent", "drafter"),
        ):
            _unwrap(await client.call_tool("chap.participant.join",
                {"workspace": "wsp_flow", "from": from_uri, "type": ptype, "role": role}))

        task_body = _unwrap(await client.call_tool("chap.task.create", {
            "workspace": "wsp_flow",
            "from": "human:alice",
            "kind": "draft_response",
            "assignee": "agent:bot",
            "input": {"subject": "test"},
        }))
        task_id = task_body["task_id"]
        assert task_id.startswith("tsk_")

        _unwrap(await client.call_tool("chap.task.update", {
            "workspace": "wsp_flow", "from": "agent:bot",
            "task_id": task_id, "state": "in_progress",
        }))

        _unwrap(await client.call_tool("chap.task.complete", {
            "workspace": "wsp_flow", "from": "agent:bot", "task_id": task_id,
            "output": {"body": "draft body", "severity": "warning"},
            "confidence": 0.85,
        }))

        _unwrap(await client.call_tool("chap.review.request", {
            "workspace": "wsp_flow", "from": "agent:bot", "task_id": task_id,
            "to": "human:alice",
            "rule": "any_one_approves",
            "artefact": {"body": "draft body", "severity": "warning"},
        }))

        override_body = _unwrap(await client.call_tool("chap.decide.override", {
            "workspace": "wsp_flow", "from": "human:alice", "task_id": task_id,
            "diff": [{"op": "replace", "path": "/severity", "value": "info"}],
            "rationale": "false positive",
            "tags": ["false-positive"],
        }))
        assert override_body["applied"]["severity"] == "info"
        assert override_body["override_artefact_id"].startswith("art_")

        ws = coord.get_workspace("wsp_flow")
        assert ws is not None
        assert ws.tasks[task_id].state == "completed"
        assert len(ws.overrides) == 1


@pytest.mark.asyncio
async def test_routing_decisions_through_mcp() -> None:
    coord = _make_coord()
    server = make_chap_mcp_server(coord)
    async with create_connected_server_and_client_session(server) as client:
        _unwrap(await client.call_tool("chap.workspace.create", {"workspace": "wsp_rt"}))
        _unwrap(await client.call_tool("chap.participant.join",
            {"workspace": "wsp_rt", "from": "human:alice", "type": "human", "role": "owner"}))
        _unwrap(await client.call_tool("chap.participant.join",
            {"workspace": "wsp_rt", "from": "agent:bot", "type": "agent", "role": "drafter"}))

        t = _unwrap(await client.call_tool("chap.task.create", {
            "workspace": "wsp_rt", "from": "human:alice", "kind": "k",
            "assignee": "agent:bot", "input": {},
            "routing_hints": {"criticality": "critical"},
        }))
        task_id = t["task_id"]

        depth = _unwrap(await client.call_tool("chap.review.depth", {
            "workspace": "wsp_rt", "from": "service:coord", "task_id": task_id,
        }))
        assert depth["depth"] == "full"
        assert depth["decision_artefact"].startswith("art_")

        esc = _unwrap(await client.call_tool("chap.escalate.auto", {
            "workspace": "wsp_rt", "from": "service:coord", "task_id": task_id,
            "default_escalation_target": "human:alice",
        }))
        assert esc["escalate"] is True
        assert esc["to"] == "human:alice"


@pytest.mark.asyncio
async def test_deliberation_through_mcp() -> None:
    coord = _make_coord()
    server = make_chap_mcp_server(coord)
    async with create_connected_server_and_client_session(server) as client:
        _unwrap(await client.call_tool("chap.workspace.create", {"workspace": "wsp_d"}))
        for u in ("human:a", "human:b", "human:c"):
            _unwrap(await client.call_tool("chap.participant.join",
                {"workspace": "wsp_d", "from": u, "type": "human", "role": "voter"}))

        d_open = _unwrap(await client.call_tool("chap.deliberate.open", {
            "workspace": "wsp_d", "from": "human:a",
            "to": ["human:a", "human:b", "human:c"],
            "rule": "quorum:2",
            "question": "ship it?",
        }))
        did = d_open["deliberation_id"]

        _unwrap(await client.call_tool("chap.deliberate.vote",
            {"workspace": "wsp_d", "from": "human:a", "deliberation_id": did, "vote": "yea"}))
        _unwrap(await client.call_tool("chap.deliberate.vote",
            {"workspace": "wsp_d", "from": "human:b", "deliberation_id": did, "vote": "yea"}))

        outcome = _unwrap(await client.call_tool("chap.deliberate.close",
            {"workspace": "wsp_d", "from": "human:a", "deliberation_id": did}))
        assert outcome["outcome"] == "approved"
        assert outcome["tally"]["yea"] == 2


@pytest.mark.asyncio
async def test_audit_verify_chain_through_mcp() -> None:
    coord = _make_coord()
    server = make_chap_mcp_server(coord)
    async with create_connected_server_and_client_session(server) as client:
        _unwrap(await client.call_tool("chap.workspace.create", {"workspace": "wsp_a"}))
        _unwrap(await client.call_tool("chap.participant.join",
            {"workspace": "wsp_a", "from": "human:alice", "type": "human", "role": "owner"}))

        result = _unwrap(await client.call_tool("chap.audit.verify_chain", {"workspace": "wsp_a"}))
        assert result["ok"] is True
        assert result["entries_checked"] >= 2
