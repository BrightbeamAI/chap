"""
MCP 2026-07-28 conformance (SEP-2575, SEP-2549, SEP-2322).

The 2026 revision drops the initialize handshake and carries version,
identity and capabilities on every request instead. A server must implement
``server/discover``, must reject a version it does not implement with
``UnsupportedProtocolVersionError``, and must type every result.

These tests drive the transport with raw JSON-RPC rather than through a
``Client``, because connecting negotiates for you and so hides which rung of
the rejection ladder a malformed request lands on.

Mirrors ``packages/coordinator-mcp/tests/discover.test.ts`` case for case, so
a divergence between the two implementations shows up as a failing test on
one side.
"""
from __future__ import annotations

from typing import Any

import anyio
import pytest
import mcp.types as types
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.mcp_server import (
    make_chap_mcp_server,
    MODERN_PROTOCOL_VERSIONS,
    SUPPORTED_PROTOCOL_VERSIONS,
)

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

UNSUPPORTED_PROTOCOL_VERSION = -32022
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601


def envelope(version: str = "2026-07-28") -> dict[str, Any]:
    """A well-formed 2026 envelope. ``clientInfo`` is optional, the rest not."""
    return {
        "_meta": {
            META_PROTOCOL_VERSION: version,
            META_CLIENT_INFO: {"name": "probe", "version": "1.0.0"},
            META_CLIENT_CAPABILITIES: {},
        }
    }


async def send_raw(method: str, params: Any = None) -> dict[str, Any]:
    """Open a fresh connection, send one raw request, return the reply.

    Fresh every time on purpose: a dual-era server settles which era a
    connection belongs to from how the client opens it, so reusing one
    connection across cases would leak the first case into the rest.
    """
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))
    server = make_chap_mcp_server(coord)
    async with create_client_server_memory_streams() as ((c_read, c_write), (s_read, s_write)):
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: server.run(s_read, s_write, server.create_initialization_options()))
            request = types.JSONRPCRequest(
                jsonrpc="2.0", id=1, method=method,
                **({"params": params} if params is not None else {}),
            )
            await c_write.send(SessionMessage(request))
            reply = await c_read.receive()
            tg.cancel_scope.cancel()
    return reply.message.model_dump(by_alias=True, exclude_none=True)


# ============================================================
#   Discovery
# ============================================================

@pytest.mark.asyncio
async def test_discover_answers_without_any_handshake() -> None:
    reply = await send_raw("server/discover", envelope())
    result = reply["result"]
    assert result["supportedVersions"] == list(SUPPORTED_PROTOCOL_VERSIONS)
    assert "2026-07-28" in result["supportedVersions"]
    assert result["resultType"] == "complete"
    assert result["capabilities"]["tools"] is not None
    assert result["_meta"][META_SERVER_INFO]["name"] == "chap"
    assert isinstance(result["instructions"], str)


@pytest.mark.asyncio
async def test_discover_is_cacheable() -> None:
    result = (await send_raw("server/discover", envelope()))["result"]
    assert result["cacheScope"] == "public"
    assert result["ttlMs"] > 0


def test_advertised_spans_both_eras_but_only_modern_is_declarable() -> None:
    # The advertised set spans both eras because CHAP serves both. The
    # declarable set is narrower: a handshake-era revision is negotiated by
    # `initialize`, never named in a per-request envelope.
    assert "2025-11-25" in SUPPORTED_PROTOCOL_VERSIONS
    assert "2025-11-25" not in MODERN_PROTOCOL_VERSIONS


@pytest.mark.asyncio
async def test_discover_unreachable_from_handshake_era_peer() -> None:
    # No envelope means the request belongs to the handshake era, where the
    # method does not exist. Answering anyway would tell a dual-era client
    # that a legacy connection can speak 2026.
    reply = await send_raw("server/discover", {})
    assert reply["error"]["code"] == METHOD_NOT_FOUND


# ============================================================
#   Version negotiation
# ============================================================

@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["1900-01-01", "2024-11-05", "2099-01-01"])
async def test_unsupported_version_is_rejected(version: str) -> None:
    error = (await send_raw("tools/list", envelope(version)))["error"]
    assert error["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert error["message"] == "Unsupported protocol version"
    assert error["data"] == {
        "supported": list(MODERN_PROTOCOL_VERSIONS),
        "requested": version,
    }


@pytest.mark.asyncio
async def test_handshake_era_version_is_not_declarable_per_request() -> None:
    # Advertised by `server/discover`, yet refused here: 2025-11-25 is
    # reached through `initialize`. The error names only versions a client
    # can retry with, so it never steers a retry back into the same refusal.
    error = (await send_raw("tools/list", envelope("2025-11-25")))["error"]
    assert error["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert error["data"]["supported"] == list(MODERN_PROTOCOL_VERSIONS)


@pytest.mark.asyncio
async def test_declared_version_without_client_capabilities_is_invalid_params() -> None:
    error = (await send_raw("tools/list", {"_meta": {META_PROTOCOL_VERSION: "2026-07-28"}}))["error"]
    assert error["code"] == INVALID_PARAMS
    assert "missing the required envelope key" in error["message"]


@pytest.mark.asyncio
async def test_non_string_version_is_invalid_params_not_a_version_error() -> None:
    error = (await send_raw("tools/list", {
        "_meta": {META_PROTOCOL_VERSION: 20260728, META_CLIENT_CAPABILITIES: {}},
    }))["error"]
    assert error["code"] == INVALID_PARAMS
    assert "must be a string" in error["message"]


@pytest.mark.asyncio
async def test_missing_envelope_key_outranks_unsupported_version() -> None:
    # Ladder order matters for cross-implementation agreement: a malformed
    # request is malformed whatever version it names.
    error = (await send_raw("tools/list", {"_meta": {META_PROTOCOL_VERSION: "1900-01-01"}}))["error"]
    assert error["code"] == INVALID_PARAMS


# ============================================================
#   Result shape, per era
# ============================================================

@pytest.mark.asyncio
async def test_list_results_are_cacheable_and_typed_for_a_modern_caller() -> None:
    result = (await send_raw("tools/list", envelope()))["result"]
    assert result["cacheScope"] == "public"
    assert result["ttlMs"] > 0
    assert result["resultType"] == "complete"
    assert len(result["tools"]) > 0


@pytest.mark.asyncio
async def test_tool_results_are_tagged_for_a_modern_caller() -> None:
    # Tool names are dotted, matching the CHAP method they dispatch to. A
    # misspelling here would fall to the unknown-tool branch and the tagging
    # assertions would pass without a real call ever being made, so the
    # success case asserts on the dispatch result too.
    ok = (await send_raw("tools/call", {
        **envelope(),
        "name": "chap.workspace.create",
        "arguments": {"workspace": "w", "profiles": ["core/1.0"]},
    }))["result"]
    assert ok["resultType"] == "complete"
    assert not ok.get("isError")
    assert '"workspace"' in ok["content"][0]["text"]

    # A call the Coordinator genuinely refuses. Reusing the workspace id above
    # would also produce an error, but for an incidental reason, so the
    # assertion would survive a regression in error surfacing.
    bad = (await send_raw("tools/call", {
        **envelope(),
        "name": "chap.workspace.describe",
        "arguments": {"workspace": "wsp_does_not_exist"},
    }))["result"]
    assert bad["resultType"] == "complete", "error results are terminal too"
    assert bad["isError"] is True
    assert '"chap_error": -32602' in bad["content"][0]["text"]

    # An unprefixed name never maps to a CHAP method, so it is refused by the
    # adapter. A `chap.`-prefixed unknown maps through and is refused by the
    # Coordinator instead; both surface as a terminal error result.
    unknown = (await send_raw("tools/call", {
        **envelope(), "name": "not.a.tool", "arguments": {},
    }))["result"]
    assert unknown["isError"] is True
    assert "Unknown CHAP tool" in unknown["content"][0]["text"]

    unknown_method = (await send_raw("tools/call", {
        **envelope(), "name": "chap.not.a.tool", "arguments": {},
    }))["result"]
    assert unknown_method["isError"] is True
    assert '"chap_error": -32601' in unknown_method["content"][0]["text"]


@pytest.mark.asyncio
async def test_handshake_era_caller_gets_its_own_result_shape() -> None:
    """`resultType`, `ttlMs` and `cacheScope` are 2026 vocabulary.

    Emitting them to a 2025-era caller would put fields on the wire that its
    own revision does not define.
    """
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))
    server = make_chap_mcp_server(coord)
    async with create_client_server_memory_streams() as ((c_read, c_write), (s_read, s_write)):
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: server.run(s_read, s_write, server.create_initialization_options()))

            async def call(request_id: int, method: str, params: Any) -> dict[str, Any]:
                await c_write.send(SessionMessage(types.JSONRPCRequest(
                    jsonrpc="2.0", id=request_id, method=method, params=params)))
                reply = await c_read.receive()
                return reply.message.model_dump(by_alias=True, exclude_none=True)

            handshake = await call(1, "initialize", {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "legacy", "version": "1.0.0"},
            })
            assert handshake["result"]["protocolVersion"] == "2025-11-25"

            await c_write.send(SessionMessage(types.JSONRPCNotification(
                jsonrpc="2.0", method="notifications/initialized")))

            listed = (await call(2, "tools/list", {}))["result"]
            assert len(listed["tools"]) > 0
            assert "resultType" not in listed
            assert "ttlMs" not in listed
            assert "cacheScope" not in listed

            called = (await call(3, "tools/call", {
                "name": "chap.workspace.create",
                "arguments": {"workspace": "w2", "profiles": ["core/1.0"]},
            }))["result"]
            assert "resultType" not in called
            assert not called.get("isError"), "a well-formed call must actually succeed"
            assert isinstance(called["content"], list)

            tg.cancel_scope.cancel()
