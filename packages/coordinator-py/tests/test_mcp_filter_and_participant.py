"""
The tool filter decides what the server offers, and ``participant`` makes
every call as one member.

Before, ``tool_filter`` only left tools out of ``tools/list``: a client that
called an excluded tool by name still ran it. And every call named its own
``from``, so a connection meant for one agent could act in any member's name.

Mirrors packages/coordinator-mcp/tests/filter_and_participant.test.ts.
"""
from __future__ import annotations

import json

import pytest
from mcp.client.client import Client

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.mcp_server import make_chap_mcp_server


def _coord() -> Coordinator:
    c = Coordinator(CoordinatorOptions(deterministic_ids=True,
                                       default_profiles=["core/1.0", "review/1.0"]))
    c.dispatch({"jsonrpc": "2.0", "id": 1, "method": "workspace.create",
                "params": {"workspace": "w", "from": "human:a",
                           "profiles": ["core/1.0", "review/1.0"]}})
    for uri, kind in (("human:a", "human"), ("agent:b", "agent")):
        c.dispatch({"jsonrpc": "2.0", "id": uri, "method": "participant.join",
                    "params": {"workspace": "w", "from": uri, "type": kind}})
    return c


def _text(result) -> str:
    return result.content[0].text if result.content else ""


@pytest.mark.asyncio
async def test_a_tool_the_filter_excludes_is_refused_as_well_as_left_out_of_the_list():
    coord = _coord()
    server = make_chap_mcp_server(coord, tool_filter=lambda name: name != "chap.task.create")
    async with Client(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert "chap.task.create" not in names
        r = await client.call_tool("chap.task.create", {
            "workspace": "w", "from": "agent:b", "kind": "k", "input": {}, "assignee": "agent:b"})
        assert r.is_error
        assert "not offered by this server: chap.task.create" in _text(r)


@pytest.mark.asyncio
async def test_participant_fills_in_from_and_refuses_a_call_that_names_another():
    coord = _coord()
    server = make_chap_mcp_server(coord, participant="agent:b")
    async with Client(server) as client:
        made = await client.call_tool("chap.task.create", {
            "workspace": "w", "kind": "k", "input": {}, "assignee": "agent:b"})
        assert not made.is_error, _text(made)
        tid = json.loads(_text(made))["task_id"]
        assert coord.get_workspace("w").tasks[tid].delegator == "agent:b"

        same = await client.call_tool("chap.task.update", {
            "workspace": "w", "from": "agent:b", "task_id": tid, "state": "in_progress"})
        assert not same.is_error, _text(same)

        other = await client.call_tool("chap.task.update", {
            "workspace": "w", "from": "human:a", "task_id": tid, "state": "declined"})
        assert other.is_error
        assert "acts as agent:b" in _text(other)
        assert coord.get_workspace("w").tasks[tid].state == "in_progress"


@pytest.mark.asyncio
async def test_without_the_new_options_every_tool_runs_and_each_call_names_its_own_from():
    coord = _coord()
    server = make_chap_mcp_server(coord)
    async with Client(server) as client:
        made = await client.call_tool("chap.task.create", {
            "workspace": "w", "from": "human:a", "kind": "k", "input": {}, "assignee": "agent:b"})
        assert not made.is_error, _text(made)
        tid = json.loads(_text(made))["task_id"]
        assert coord.get_workspace("w").tasks[tid].delegator == "human:a"


@pytest.mark.asyncio
async def test_with_participant_the_tools_list_from_as_optional_and_say_whose_name_it_carries():
    server = make_chap_mcp_server(_coord(), participant="agent:b")
    async with Client(server) as client:
        update = next(t for t in (await client.list_tools()).tools if t.name == "chap.task.update")
        assert "from" not in update.input_schema["required"]
        assert "task_id" in update.input_schema["required"]
        assert "acts as agent:b" in update.input_schema["properties"]["from"]["description"]


class _Awaiting:
    def __init__(self, coord: Coordinator) -> None:
        self.coord = coord

    async def dispatch(self, envelope):
        return self.coord.dispatch(envelope)


@pytest.mark.asyncio
async def test_a_dispatcher_that_answers_with_an_awaitable_is_awaited():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    async with Client(make_chap_mcp_server(_Awaiting(coord))) as client:
        r = await client.call_tool("chap.workspace.create", {"workspace": "w", "from": "human:a"})
        assert not r.is_error, _text(r)
        assert json.loads(_text(r))["workspace"] == "w"
