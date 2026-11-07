"""
The executor runs the skills its filter keeps, and refuses the others with
-32601; the Agent Card lists the same set. Unset, the filter keeps the
agent's skills, ``AGENT_MCP_TOOLS``.

Mirrors packages/coordinator-a2a/tests/skill_filter.test.ts.
"""
from __future__ import annotations

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import Message, Part, Role, SendMessageRequest, Task
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.a2a_server import (
    dispatch_a2a_message,
    make_chap_agent_card,
    make_chap_agent_executor,
)
from chap_coordinator.transports.mcp_schemas import TOOL_NAMES
from chap_coordinator.transports.mcp_tools import AGENT_MCP_TOOLS


class _Queue(EventQueue):
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:  # type: ignore[no-untyped-def]
        self.events.append(event)


def _message(skill: str, params: dict) -> Message:
    data = Value()
    ParseDict({"skill": skill, "params": params}, data)
    return Message(message_id="m", role=Role.ROLE_USER, parts=[Part(data=data)])


async def _run(executor, message: Message) -> dict:
    """The JSON the executor answered with: an answer's own, or a refused task's status message."""
    queue = _Queue()
    ctx = RequestContext(call_context=ServerCallContext(),
                         request=SendMessageRequest(message=message), context_id="c")
    await executor.execute(ctx, queue)
    event = queue.events[0]
    reply = event.status.message if isinstance(event, Task) else event
    return MessageToDict(reply.parts[0].data)


@pytest.mark.asyncio
async def test_a_skill_the_filter_excludes_is_refused_and_runs_nothing():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    executor = make_chap_agent_executor(coord, skill_filter=lambda s: s != "chap.workspace.create")
    body = await _run(executor, _message("chap.workspace.create", {"workspace": "w"}))
    assert body["chap_error"] == -32601
    assert "not offered by this server: chap.workspace.create" in body["message"]
    assert coord.get_workspace("w") is None


@pytest.mark.asyncio
async def test_a_skill_the_filter_keeps_runs():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    executor = make_chap_agent_executor(coord, skill_filter=lambda s: s == "chap.workspace.create")
    body = await _run(executor, _message("chap.workspace.create", {"workspace": "w"}))
    assert body["workspace"] == "w"


@pytest.mark.asyncio
async def test_without_a_filter_the_executor_runs_the_agents_skills_only_and_the_card_lists_those():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, default_profiles=["core/1.0", "control/1.0"]))
    coord.dispatch({"jsonrpc": "2.0", "id": 1, "method": "workspace.create", "params": {"workspace": "w", "from": "agent:o"}})
    coord.dispatch({"jsonrpc": "2.0", "id": 2, "method": "participant.join",
                    "params": {"workspace": "w", "from": "agent:o", "type": "agent"}})
    executor = make_chap_agent_executor(coord, participant="agent:o")
    for skill in ("chap.control.pause", "chap.participant.join", "chap.workspace.set_profiles", "chap.review.request"):
        body = await _run(executor, _message(skill, {"workspace": "w", "scope": "workspace", "type": "agent",
                                                     "profiles": ["core/1.0"]}))
        assert body["chap_error"] == -32601, skill
        assert "not offered by this server" in body["message"], skill
    assert coord.get_workspace("w").state == "active"
    assert (await _run(executor, _message("chap.workspace.describe", {"workspace": "w"})))["id"] == "w"

    card = make_chap_agent_card(base_url="http://127.0.0.1:9090/")
    assert {s.id for s in card.skills} == AGENT_MCP_TOOLS


@pytest.mark.asyncio
async def test_a_filter_that_keeps_every_skill_offers_every_skill():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    body = await _run(make_chap_agent_executor(coord, skill_filter=lambda _name: True),
                      _message("chap.workspace.create", {"workspace": "w"}))
    assert body["workspace"] == "w"
    assert len(make_chap_agent_card(base_url="http://127.0.0.1:9090/", skill_filter=lambda _name: True).skills) \
        == len(TOOL_NAMES)


def test_dispatch_refuses_an_excluded_skill():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    reply = dispatch_a2a_message(coord, _message("chap.workspace.create", {"workspace": "w"}),
                                 skill_filter=lambda s: False)
    assert reply["error"]["code"] == -32601
