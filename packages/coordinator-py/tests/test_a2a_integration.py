"""
Integration tests for the A2A server adapter.

The A2A SDK doesn't ship an in-memory transport like MCP does, so we
exercise the dispatch path directly: build an :class:`AgentExecutor`,
hand it a :class:`RequestContext` carrying a Message, capture the
events it publishes to an :class:`EventQueue`, and verify the
response shape.

Mirrors the TS A2A integration tests structurally; same fixtures,
same assertions, different SDK.
"""
from __future__ import annotations

import json

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import Message, Part, Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.a2a_server import (
    ChapAgentExecutor,
    dispatch_a2a_message,
    make_chap_agent_card,
    make_chap_agent_executor,
)
from chap_coordinator.transports.mcp_schemas import TOOL_NAMES


class RecordingEventQueue(EventQueue):
    """Test helper: collect events instead of routing them.

    Implements the abstract :class:`EventQueue` so it works with the
    SDK's async enqueue contract.
    """
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:  # type: ignore[no-untyped-def]
        self.events.append(event)


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


def _build_message(skill_id: str, params: dict, *, message_id: str = "m") -> Message:
    data = Value()
    ParseDict({"skill": skill_id, "params": params}, data)
    return Message(
        message_id=message_id,
        role=Role.ROLE_USER,
        parts=[Part(data=data)],
    )


def _extract_data(msg: Message) -> dict:
    for part in msg.parts:
        if part.WhichOneof("content") == "data":
            return MessageToDict(part.data)
    raise AssertionError("response message had no data part")


async def _run(executor: ChapAgentExecutor, message: Message,
               context_id: str = "ctx", task_id: str = "") -> list:
    """Run executor.execute() and return the events it published."""
    queue = RecordingEventQueue()
    req = SendMessageRequest(message=message)
    ctx = RequestContext(
        call_context=ServerCallContext(),
        request=req,
        context_id=context_id,
        task_id=task_id or None,
    )
    await executor.execute(ctx, queue)
    return queue.events


# ============================================================
# AgentCard
# ============================================================

def test_agent_card_lists_every_method() -> None:
    card = make_chap_agent_card(base_url="http://localhost:9000")
    assert len(card.skills) == len(TOOL_NAMES)
    skill_ids = {s.id for s in card.skills}
    assert "chap.workspace.create" in skill_ids
    assert "chap.task.create" in skill_ids
    assert "chap.decide.override" in skill_ids
    assert "chap.deliberate.open" in skill_ids
    # Interface and version sanity
    assert card.supported_interfaces[0].url == "http://localhost:9000"
    assert card.supported_interfaces[0].protocol_version == "1.0"


def test_agent_card_descriptions_are_non_trivial() -> None:
    card = make_chap_agent_card(base_url="http://localhost:9000")
    for skill in card.skills:
        assert skill.description, f"empty description on {skill.id}"
        assert len(skill.description) > 20, f"trivial description on {skill.id}"


# ============================================================
# dispatch_a2a_message: direct functional path
# ============================================================

def test_dispatch_workspace_create_via_skill_param() -> None:
    coord = _make_coord()
    msg = _build_message("chap.workspace.create", {"workspace": "wsp_a"})
    resp = dispatch_a2a_message(coord, msg)
    assert "result" in resp
    assert resp["result"]["workspace"] == "wsp_a"
    assert coord.get_workspace("wsp_a") is not None


def test_dispatch_skill_via_metadata_takes_precedence() -> None:
    # Skill in metadata; params in data
    coord = _make_coord()
    data = Value()
    ParseDict({"workspace": "wsp_b"}, data)
    metadata = Value()
    ParseDict({"skill": "chap.workspace.create"}, metadata)
    msg = Message(message_id="m", role=Role.ROLE_USER, parts=[Part(data=data)])
    # The protobuf metadata field is a Struct; build via ParseDict directly
    from google.protobuf.struct_pb2 import Struct
    md = Struct()
    ParseDict({"skill": "chap.workspace.create"}, md)
    msg.metadata.CopyFrom(md)

    resp = dispatch_a2a_message(coord, msg)
    assert "result" in resp, f"unexpected: {resp}"
    assert resp["result"]["workspace"] == "wsp_b"


def test_dispatch_unknown_skill_returns_method_not_found() -> None:
    coord = _make_coord()
    msg = _build_message("not.a.chap.skill", {})
    resp = dispatch_a2a_message(coord, msg)
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_dispatch_chap_error_returns_intact() -> None:
    coord = _make_coord()
    msg = _build_message("chap.workspace.describe", {"workspace": "missing"})
    resp = dispatch_a2a_message(coord, msg)
    assert "error" in resp
    assert resp["error"]["code"] == -32602


# ============================================================
# ChapAgentExecutor: full async path
# ============================================================

@pytest.mark.asyncio
async def test_executor_publishes_success_as_data_message() -> None:
    coord = _make_coord()
    executor = make_chap_agent_executor(coord)
    msg = _build_message("chap.workspace.create", {"workspace": "wsp_x"})
    events = await _run(executor, msg)

    assert len(events) == 1, f"expected 1 event, got {len(events)}"
    payload = _extract_data(events[0])
    assert payload["workspace"] == "wsp_x"


@pytest.mark.asyncio
async def test_executor_publishes_error_with_is_error_metadata() -> None:
    coord = _make_coord()
    executor = make_chap_agent_executor(coord)
    msg = _build_message("chap.workspace.describe", {"workspace": "nope"})
    events = await _run(executor, msg)

    assert len(events) == 1
    payload = _extract_data(events[0])
    assert payload["chap_error"] == -32602

    # Check the is_error flag in metadata
    part = events[0].parts[0]
    if len(part.metadata.fields):
        md = MessageToDict(part.metadata)
        assert md.get("is_error") is True


@pytest.mark.asyncio
async def test_executor_full_workflow() -> None:
    coord = _make_coord()
    executor = make_chap_agent_executor(coord)

    # workspace.create
    msg = _build_message("chap.workspace.create", {"workspace": "wsp_flow"})
    events = await _run(executor, msg)
    assert _extract_data(events[0])["workspace"] == "wsp_flow"

    # 3 participants
    for from_uri, ptype, role in (
        ("human:alice", "human", "owner"),
        ("human:bob",   "human", "reviewer"),
        ("agent:bot",   "agent", "drafter"),
    ):
        msg = _build_message("chap.participant.join", {
            "workspace": "wsp_flow", "from": from_uri, "type": ptype, "role": role,
        })
        events = await _run(executor, msg)
        assert _extract_data(events[0])["joined"] is True

    # task.create
    msg = _build_message("chap.task.create", {
        "workspace": "wsp_flow",
        "from": "human:alice",
        "kind": "draft_response",
        "assignee": "agent:bot",
        "input": {"subject": "test"},
    })
    events = await _run(executor, msg)
    task_id = _extract_data(events[0])["task_id"]
    assert task_id.startswith("tsk_")

    # task.update -> in_progress
    msg = _build_message("chap.task.update", {
        "workspace": "wsp_flow", "from": "agent:bot",
        "task_id": task_id, "state": "in_progress",
    })
    events = await _run(executor, msg)
    assert _extract_data(events[0])["state"] == "in_progress"

    # task.complete
    msg = _build_message("chap.task.complete", {
        "workspace": "wsp_flow", "from": "agent:bot", "task_id": task_id,
        "output": {"body": "draft", "severity": "warning"}, "confidence": "0.9",
    })
    events = await _run(executor, msg)
    assert _extract_data(events[0])["state"] == "completed"

    # review.request
    msg = _build_message("chap.review.request", {
        "workspace": "wsp_flow", "from": "agent:bot", "task_id": task_id,
        "to": "human:alice", "rule": "any_one_approves",
        "artefact": {"body": "draft", "severity": "warning"},
    })
    events = await _run(executor, msg)
    assert _extract_data(events[0])["state"] == "review_requested"

    # decide.override
    msg = _build_message("chap.decide.override", {
        "workspace": "wsp_flow", "from": "human:alice", "task_id": task_id,
        "diff": [{"op": "replace", "path": "/severity", "value": "info"}],
        "rationale": "false positive", "tags": ["false-positive"],
    })
    events = await _run(executor, msg)
    body = _extract_data(events[0])
    assert body["applied"]["severity"] == "info"
    assert body["override_artefact_id"].startswith("art_")

    # Verify against the underlying Coordinator
    ws = coord.get_workspace("wsp_flow")
    assert ws is not None
    assert ws.tasks[task_id].state == "completed"
    assert len(ws.overrides) == 1


@pytest.mark.asyncio
async def test_executor_deliberation_flow() -> None:
    coord = _make_coord()
    executor = make_chap_agent_executor(coord)

    msg = _build_message("chap.workspace.create", {"workspace": "wsp_d"})
    await _run(executor, msg)
    for u in ("human:a", "human:b", "human:c"):
        msg = _build_message("chap.participant.join",
            {"workspace": "wsp_d", "from": u, "type": "human", "role": "voter"})
        await _run(executor, msg)

    msg = _build_message("chap.deliberate.open", {
        "workspace": "wsp_d", "from": "human:a",
        "to": ["human:a", "human:b", "human:c"],
        "rule": "quorum:2", "question": "ship it?",
    })
    events = await _run(executor, msg)
    did = _extract_data(events[0])["deliberation_id"]

    for voter in ("human:a", "human:b"):
        msg = _build_message("chap.deliberate.vote",
            {"workspace": "wsp_d", "from": voter, "deliberation_id": did, "vote": "yea"})
        await _run(executor, msg)

    msg = _build_message("chap.deliberate.close",
        {"workspace": "wsp_d", "from": "human:a", "deliberation_id": did})
    events = await _run(executor, msg)
    body = _extract_data(events[0])
    assert body["outcome"] == "approved"
    assert body["tally"]["yea"] == 2
