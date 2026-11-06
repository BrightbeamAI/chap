"""
chap_coordinator.transports.remote
===================================

Forwarding to a running CHAP server.

``python -m chap_coordinator.transports.mcp_cli --url <endpoint>`` serves MCP
on stdio and sends each call on to the JSON-RPC endpoint of a server that
already runs a coordinator, such as a desk server's ``POST /chap``. The
workspace, its store and the people deciding in the desk all stay with that
server.

Mirrors ``packages/coordinator-mcp/src/remote.ts``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import anyio.to_thread

__all__ = ["ChapEndpointError", "RemoteDispatcher"]


class ChapEndpointError(Exception):
    """The endpoint could not be reached, or did not answer in JSON-RPC.

    The message is written for the MCP client, which shows it to the
    assistant.
    """


class RemoteDispatcher:
    """POST each envelope to ``endpoint`` and return the JSON-RPC response.

    A refusal comes back as the server's own error envelope; only a failure
    to get a JSON-RPC answer at all raises :class:`ChapEndpointError`.
    """

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    async def dispatch(self, envelope: dict[str, Any]) -> dict[str, Any]:
        # urllib blocks, so the request runs on a worker thread and the event
        # loop keeps serving the MCP client meanwhile.
        return await anyio.to_thread.run_sync(self._post, envelope)

    def _post(self, envelope: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(envelope).encode("utf-8"),
            headers={"content-type": "application/json", "accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request) as response:  # noqa: S310 - the operator's own --url
                status, text = response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as err:
            status, text = err.code, err.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as err:
            reason = getattr(err, "reason", err)
            raise ChapEndpointError(
                f"The CHAP server at {self.endpoint} could not be reached ({reason}). Start it, or check --url."
            ) from None
        try:
            body = json.loads(text)
        except ValueError:
            raise ChapEndpointError(
                f"The CHAP server at {self.endpoint} answered HTTP {status} with a body that is not JSON."
            ) from None
        if _is_response_envelope(body):
            return body
        said = f": {body['error']}" if isinstance(body, dict) and isinstance(body.get("error"), str) else ""
        raise ChapEndpointError(f"The CHAP server at {self.endpoint} refused the call with HTTP {status}{said}")


def _is_response_envelope(body: Any) -> bool:
    return (isinstance(body, dict) and body.get("jsonrpc") == "2.0"
            and ("result" in body or isinstance(body.get("error"), dict)))
