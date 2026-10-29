"""
The executor refuses the skills its filter excludes.

``skill_filter`` was an option of the Agent Card alone, which only lists
skills: the executor ran any ``chap.<method>`` it was sent. The executor now
takes the same filter and refuses what it excludes with -32601.

Mirrors packages/coordinator-a2a/tests/skill_filter.test.ts.
"""
from __future__ import annotations

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import Message, Part, Role, SendMessageRequest
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.a2a_server import (
    dispatch_a2a_message,
    make_chap_agent_executor,
)


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
    queue = _Queue()
    ctx = RequestContext(call_context=ServerCallContext(),
                         request=SendMessageRequest(message=message), context_id="c")
    await executor.execute(ctx, queue)
    return MessageToDict(queue.events[0].parts[0].data)


@pytest.mark.asyncio
async def test_a_skill_the_filter_excludes_is_refused_and_runs_nothing():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    executor = make_chap_agent_executor(coord, skill_filter=lambda s: s != "chap.workspace.create")
    body = await _run(executor, _message("chap.workspace.create", {"workspace": "w"}))
    assert body["chap_error"] == -32601
    assert "not offered by this server: chap.workspace.create" in body["message"]
    assert coord.get_workspace("w") is None


@pytest.mark.asyncio
async def test_a_skill_the_filter_keeps_runs_as_before():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    executor = make_chap_agent_executor(coord, skill_filter=lambda s: s == "chap.workspace.create")
    body = await _run(executor, _message("chap.workspace.create", {"workspace": "w"}))
    assert body["workspace"] == "w"


@pytest.mark.asyncio
async def test_without_a_filter_every_skill_runs():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    body = await _run(make_chap_agent_executor(coord),
                      _message("chap.workspace.create", {"workspace": "w"}))
    assert body["workspace"] == "w"


def test_dispatch_refuses_an_excluded_skill():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    reply = dispatch_a2a_message(coord, _message("chap.workspace.create", {"workspace": "w"}),
                                 skill_filter=lambda s: False)
    assert reply["error"]["code"] == -32601
