"""
A dispatcher that throws is a fault in the server, and its message can carry
a path or a stack line. The tool error repeated it ("CHAP dispatch threw:
<message>"); it now says only that the call failed inside the server, and the
error goes to the server's log.

Mirrors packages/coordinator-mcp/tests/dispatch_errors.test.ts.
"""
from __future__ import annotations

import json
import logging

import pytest
from mcp.client.client import Client

from chap_coordinator.transports.mcp_server import make_chap_mcp_server


class _Faulty:
    def dispatch(self, envelope):
        raise OSError("[Errno 2] No such file or directory: '/srv/chap/data/chap.db'")


@pytest.mark.asyncio
async def test_a_dispatchers_exception_text_stays_in_the_servers_log(caplog):
    server = make_chap_mcp_server(_Faulty())
    with caplog.at_level(logging.ERROR, logger="chap_coordinator.transports.mcp_server"):
        async with Client(server) as client:
            r = await client.call_tool("chap.workspace.describe", {"workspace": "w"})
    assert r.is_error
    assert r.content[0].text == "The CHAP call failed inside the server; its log has the detail."
    assert "/srv/chap" not in r.content[0].text
    assert any(rec.exc_info and "/srv/chap" in str(rec.exc_info[1]) for rec in caplog.records), \
        "the error is logged"


class _Internal:
    """Answers as the coordinator does when a handler raises."""

    def dispatch(self, envelope):
        return {"jsonrpc": "2.0", "id": envelope["id"],
                "error": {"code": -32603, "message": "Internal error",
                          "data": {"detail": "unhashable type: 'list' in /srv/chap/coordinator.py"}}}


@pytest.mark.asyncio
async def test_a_coordinators_internal_error_reaches_the_client_without_its_detail(caplog):
    # The coordinators answer a fault in a handler with -32603 and the
    # exception's text in data.detail; the tool error passed that on.
    with caplog.at_level(logging.ERROR, logger="chap_coordinator.transports.mcp_server"):
        async with Client(make_chap_mcp_server(_Internal())) as client:
            r = await client.call_tool("chap.workspace.describe", {"workspace": "w"})
    assert r.is_error
    assert json.loads(r.content[0].text) == {
        "chap_error": -32603, "message": "The CHAP call failed inside the server; its log has the detail."}
    assert "unhashable type" in caplog.text


class _Refusing:
    def dispatch(self, envelope):
        return {"jsonrpc": "2.0", "id": envelope["id"],
                "error": {"code": -32011, "message": "Not an addressed reviewer for this task: agent:b",
                          "data": {"reviewers": ["human:a"]}}}


@pytest.mark.asyncio
async def test_a_refusal_is_passed_on_as_the_coordinator_gave_it_data_included():
    async with Client(make_chap_mcp_server(_Refusing())) as client:
        r = await client.call_tool("chap.decide.approve", {"workspace": "w"})
    assert json.loads(r.content[0].text) == {
        "chap_error": -32011, "message": "Not an addressed reviewer for this task: agent:b",
        "data": {"reviewers": ["human:a"]}}
