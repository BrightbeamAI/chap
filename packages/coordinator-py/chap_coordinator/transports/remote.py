"""
chap_coordinator.transports.remote
===================================

Forwarding to a running CHAP server.

``python -m chap_coordinator.transports.mcp_cli --url <endpoint>`` serves MCP
on stdio and sends each call on to the JSON-RPC endpoint of a server that
already runs a coordinator, such as a desk server's ``POST /chap``. The
workspace, its store and the people deciding in the desk all stay with that
server.

The HTTP client is :class:`chap_coordinator.client.HttpCoordinator`, the
one the desk's agents use. It blocks, so each call runs on a worker thread
and the event loop keeps serving the MCP client meanwhile.

Mirrors ``packages/coordinator-mcp/src/remote.ts``.
"""
from __future__ import annotations

from typing import Any

import anyio.to_thread

from chap_coordinator.client import ChapEndpointError, HttpCoordinator

__all__ = ["ChapEndpointError", "RemoteDispatcher"]


class RemoteDispatcher:
    """POST each envelope to ``endpoint`` and return the JSON-RPC response.

    A refusal comes back as the server's own error envelope; only a failure
    to get a JSON-RPC answer at all raises :class:`ChapEndpointError`, whose
    message is written for the MCP client, which shows it to the assistant.
    """

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self._client = HttpCoordinator(endpoint)

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(self._client.dispatch, envelope)
