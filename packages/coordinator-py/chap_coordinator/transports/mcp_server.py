"""
chap_coordinator.transports.mcp_server
=======================================

MCP server adapter for a CHAP Coordinator. Wraps a Coordinator
instance and exposes every CHAP method as an MCP tool.

Spec target: MCP 2026-07-28, serving handshake-era clients as well.

The 2026-07-28 revision is stateless: instead of negotiating once through
an ``initialize`` handshake, every request carries its protocol version and
client capabilities in ``_meta``, and the server accepts or rejects each
request on its own. The ``mcp`` 2.x SDK implements that boundary natively,
so this adapter inherits ``server/discover``, per-request version rejection
with ``UnsupportedProtocolVersionError``, envelope validation, era-aware
``resultType`` tagging, and cacheable list results. A handshake-era client
negotiating ``2025-11-25`` through ``initialize`` continues to work
unchanged on the same server.

Two version sets, deliberately different, matching the TypeScript adapter:

- Only ``2026-07-28`` may be declared in a per-request envelope, because
  only revisions that use per-request metadata can be. A request naming
  anything else is refused with ``-32022``, whose payload lists just the
  declarable versions so a retry never lands on the same refusal.

- ``server/discover`` advertises ``2026-07-28`` and ``2025-11-25``, because
  CHAP serves both eras. The SDK's default handler advertises only the
  modern set, so this module replaces it.

Usage (stdio)::

    from chap_coordinator import Coordinator
    from chap_coordinator.transports.mcp_server import make_chap_mcp_server
    from mcp.server.stdio import stdio_server

    coord = Coordinator()
    server = make_chap_mcp_server(coord, name="chap", version="0.2.12")

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

from mcp.server import CacheHint, Server
from mcp.server.context import ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    DiscoverResult,
    ListToolsResult,
    PaginatedRequestParams,
    RequestParams,
    TextContent,
    Tool,
)

from chap_coordinator.coordinator import Coordinator

from .mcp_schemas import SCHEMAS, TOOL_NAMES, method_for_tool, coerce_tool_args
from .mcp_tools import TOOL_DESCRIPTIONS


#: Revisions that may be declared in a per-request ``_meta`` envelope.
MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)

#: Revisions advertised by ``server/discover``, newest first. Spans both
#: eras because CHAP serves both.
SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25")

#: Freshness hint for cacheable list results. The CHAP tool surface is fixed
#: at construction, so a long TTL is safe.
LIST_TTL_MS = 3_600_000

_LIST_CACHE_HINT = CacheHint(ttl_ms=LIST_TTL_MS, scope="public")

_INSTRUCTIONS = (
    "CHAP Coordinator exposed as MCP tools. Every tool call is recorded "
    "on the CHAP audit log; governed methods enforce membership and "
    "review rules server-side."
)

__all__ = [
    "make_chap_mcp_server",
    "MODERN_PROTOCOL_VERSIONS",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "LIST_TTL_MS",
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
    version: str = "0.2.12",
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
    if envelope_id_factory is None:
        counter = count(1)

        def _next_id() -> str:
            return f"mcp-{next(counter)}"

        next_id = _next_id
    else:
        next_id = envelope_id_factory

    filter_fn = tool_filter or (lambda _name: True)

    enabled = [
        tool_name
        for tool_name in TOOL_NAMES
        if filter_fn(tool_name) and method_for_tool(tool_name) is not None
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

    async def _list_tools(
        ctx: ServerRequestContext[Any],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        return ListToolsResult(tools=tool_list)

    async def _call_tool(
        ctx: ServerRequestContext[Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        tool_name = params.name
        arguments = params.arguments or {}

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
            "params": coerce_tool_args(tool_name, arguments),
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

    server: Server = Server(
        name,
        version=version,
        instructions=_INSTRUCTIONS,
        # SEP-2549. The SDK stamps ttlMs and cacheScope onto these results
        # for a modern caller and withholds them from a handshake-era one.
        cache_hints={
            "tools/list": _LIST_CACHE_HINT,
            "server/discover": _LIST_CACHE_HINT,
        },
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )

    async def _discover(
        ctx: ServerRequestContext[Any],
        params: RequestParams | None,
    ) -> DiscoverResult:
        """Advertise both eras.

        The SDK default names only the modern set. CHAP also serves
        handshake-era clients, so both are advertised; which mechanism
        reaches which revision is settled by the version rules above, and a
        request declaring the handshake revision is still refused.
        """
        return DiscoverResult(
            supported_versions=list(SUPPORTED_PROTOCOL_VERSIONS),
            capabilities=server.get_capabilities(protocol_version=ctx.protocol_version),
            instructions=_INSTRUCTIONS,
        )

    server.add_request_handler("server/discover", RequestParams, _discover)

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
