"""
chap_coordinator.transports.mcp_server
=======================================

MCP server adapter for a CHAP Coordinator. Wraps a Coordinator
instance and exposes every CHAP method as an MCP tool.

Spec target: MCP 2025-11-25 (current stable). CHAP 0.2.

Usage (stdio)::

    from chap_coordinator import Coordinator
    from chap_coordinator.transports.mcp_server import make_chap_mcp_server
    from mcp.server.stdio import stdio_server

    coord = Coordinator()
    server = make_chap_mcp_server(coord, name="chap", version="0.2.3")

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

Architecture notes
------------------

- One Coordinator -> one MCP server. Multi-workspace is handled
  inside the Coordinator (workspaces are addressable by id).

- The adapter holds no state. Every tool call translates to a
  JSON-RPC envelope and dispatches through ``coord.dispatch()``.

- Tool naming follows ``chap.<method>`` so the prefix avoids
  collisions with other MCP servers a client might load.

- Tool inputs are described by JSON Schemas (not Pydantic models)
  and are passed through to the Coordinator without re-validating
  at the MCP layer. The Coordinator's own dispatch validates params
  and returns spec-correct JSON-RPC error codes, which we surface
  as MCP tool errors. This keeps the schema definitions
  single-sourced.

- Authentication is intentionally out of scope at this layer.
  Apply OAuth 2.1 / Streamable HTTP auth at the transport layer
  per MCP's auth model.
"""
from __future__ import annotations

import json
from itertools import count
from typing import Any, Callable, Optional

from mcp.server import Server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

from chap_coordinator.coordinator import Coordinator

from .mcp_schemas import SCHEMAS, TOOL_NAMES, method_for_tool, coerce_tool_args
from .mcp_tools import TOOL_DESCRIPTIONS


__all__ = [
    "make_chap_mcp_server",
    "dispatch_tool_call",
    "SCHEMAS",
    "TOOL_NAMES",
    "TOOL_DESCRIPTIONS",
    "method_for_tool",
    "coerce_tool_args",
]


def make_chap_mcp_server(
    coord: Coordinator,
    *,
    name: str = "chap",
    version: str = "0.2.3",
    tool_filter: Optional[Callable[[str], bool]] = None,
    envelope_id_factory: Optional[Callable[[], Any]] = None,
) -> Server:
    """Wrap a CHAP Coordinator as an MCP server.

    The returned :class:`mcp.server.Server` has ``tools/list`` and
    ``tools/call`` handlers registered for every CHAP method; pass it
    to a transport (stdio, Streamable HTTP) to start serving.

    Parameters
    ----------
    coord
        The Coordinator instance to wrap.
    name
        Server name advertised to MCP clients.
    version
        Server version string.
    tool_filter
        Optional predicate to restrict which CHAP methods are exposed.
        Default: expose all 39.
    envelope_id_factory
        Optional callable returning a fresh envelope id per call.
        Default: a counter producing ``"mcp-1"``, ``"mcp-2"``, ...
    """
    server = Server(name=name, version=version)

    if envelope_id_factory is None:
        counter = count(1)
        def _next_id() -> str:
            return f"mcp-{next(counter)}"
        next_id = _next_id
    else:
        next_id = envelope_id_factory

    filter_fn = tool_filter or (lambda _name: True)

    enabled = [
        name
        for name in TOOL_NAMES
        if filter_fn(name) and method_for_tool(name) is not None
    ]

    tool_list: list[Tool] = [
        Tool(
            name=tool_name,
            title=tool_name,
            description=TOOL_DESCRIPTIONS.get(tool_name, f"CHAP method {method_for_tool(tool_name)}."),
            inputSchema=SCHEMAS[tool_name],
        )
        for tool_name in enabled
    ]

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return tool_list

    @server.call_tool(validate_input=False)
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        method = method_for_tool(tool_name)
        if method is None:
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=f"Unknown CHAP tool: {tool_name}")],
            )

        envelope = {
            "jsonrpc": "2.0",
            "id": next_id(),
            "method": method,
            "params": coerce_tool_args(tool_name, arguments or {}),
        }

        try:
            response = coord.dispatch(envelope)
        except Exception as exc:  # noqa: BLE001 - we want to surface any handler bug
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=f"CHAP dispatch threw: {exc}")],
            )

        if "error" in response:
            err = response["error"]
            body: dict[str, Any] = {
                "chap_error": err.get("code"),
                "message": err.get("message", ""),
            }
            if "data" in err:
                body["data"] = err["data"]
            return CallToolResult(
                isError=True,
                content=[TextContent(type="text", text=json.dumps(body, indent=2, default=str))],
            )

        return CallToolResult(
            content=[TextContent(
                type="text",
                text=json.dumps(response.get("result"), indent=2, default=str),
            )],
        )

    return server


def dispatch_tool_call(
    coord: Coordinator,
    tool_name: str,
    arguments: dict[str, Any],
    envelope_id: Any = "mcp-call",
) -> dict[str, Any]:
    """Lower-level helper: translate a tool call to a CHAP envelope
    and back. Useful for tests and for embedding the adapter inside a
    larger MCP server that registers its own additional tools.
    """
    method = method_for_tool(tool_name)
    if method is None:
        return {
            "jsonrpc": "2.0",
            "id": envelope_id,
            "error": {"code": -32601, "message": f"Unknown CHAP tool: {tool_name}"},
        }
    return coord.dispatch({
        "jsonrpc": "2.0",
        "id": envelope_id,
        "method": method,
        "params": coerce_tool_args(tool_name, arguments or {}),
    })
