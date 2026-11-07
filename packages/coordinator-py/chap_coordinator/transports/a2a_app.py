"""
chap_coordinator.transports.a2a_app
====================================

The CHAP A2A server as an ASGI app, with the checks an A2A server on the
user's machine needs before any route.

A browser will send a request to a loopback address from any page, so
without the checks a page the user visits can call the coordinator: a
cross-site ``fetch`` with ``mode: "no-cors"`` and a ``text/plain`` body is a
request no preflight stops. :class:`ChapA2aGuard` makes the create-chap-app
servers' checks, as ``chapA2aGuard`` does for the TypeScript adapter:

- a Host that is not one of the server's own names is refused with 421, so
  a page that rebinds its own name to this address (DNS rebinding) gets
  nothing;
- an Origin naming another site is refused with 403; a client that is not a
  browser sends no Origin, and is let through;
- a POST must be ``application/json`` (415);
- a body over the limit is refused with 413 before more of it is read, one
  that is not JSON is a parse error (400), and one that nests deeper than
  32 levels is refused with 400, before any parser deeper in runs out of
  stack;
- an exception reaches the client as a short JSON body, and its detail goes
  to the log.

Usage::

    import uvicorn
    from chap_coordinator import Coordinator
    from chap_coordinator.transports.a2a_app import make_chap_a2a_app

    app = make_chap_a2a_app(Coordinator(), base_url="http://127.0.0.1:9090/",
                            participant="agent:orchestrator@local")
    uvicorn.run(app, host="127.0.0.1", port=9090)

``python -m chap_coordinator.transports.a2a_cli`` serves this app.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Iterable, MutableMapping, Optional
from urllib.parse import urlsplit

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from starlette.applications import Starlette

from chap_coordinator.coordinator import Coordinator

from .a2a_server import ChapTaskStore, make_chap_agent_card, make_chap_agent_executor

__all__ = ["ChapA2aGuard", "DEFAULT_MAX_BODY_BYTES", "make_chap_a2a_app"]

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: The largest body read: the coordinator's 1 MiB envelope limit, and room
#: for the A2A message around it. The same as the TypeScript guard's.
DEFAULT_MAX_BODY_BYTES = 1_048_576 + 65_536

#: The deepest a body may nest its arrays and objects. An A2A message holds a
#: call's params six levels down, so this leaves them 26, more than any CHAP
#: call uses, and keeps every body within what a2a-sdk's protobuf parser
#: takes, about 50 levels; deeper still, json.loads runs out of recursion.
#: The TypeScript guard takes the same limit, so the two servers refuse the
#: same bodies.
_MAX_BODY_DEPTH = 32


class _BodyTooLarge(Exception):
    pass


class ChapA2aGuard:
    """ASGI middleware making the checks above before ``app`` sees a request.

    ``allowed_hosts`` are names the server also answers to, each as a Host
    header carries it: ``a2a.internal:9090``, or a bare name behind a proxy
    on the default port. Its loopback names on its own port, 127.0.0.1,
    localhost and [::1], are always allowed.
    """

    def __init__(self, app: ASGIApp, *, allowed_hosts: Iterable[str] = (),
                 max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[str, str] = {}
        origins = 0
        for name, value in scope["headers"]:
            key = name.decode("latin-1").lower()
            origins += key == "origin"
            headers.setdefault(key, value.decode("latin-1"))
        port = (scope.get("server") or (None, None))[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", *self.allowed_hosts}
        host = headers.get("host", "").lower()
        if host not in allowed:
            await _refuse(send, 421, {"error": "unknown host; add it to the allowed hosts to serve under that name"})
            return
        # Two Origin headers name no one origin, and are refused as the
        # TypeScript guard refuses them.
        if origins > 1 or _foreign_origin(headers.get("origin"), allowed, host):
            await _refuse(send, 403, {"error": "cross-origin requests are refused"})
            return
        if scope["method"] != "POST":
            await self._run(scope, receive, send)
            return
        if not headers.get("content-type", "").lower().startswith("application/json"):
            await _refuse(send, 415, {"error": "POST takes application/json"})
            return
        try:
            body = await self._read(headers, receive)
        except _BodyTooLarge:
            # The rest of the body is never read, so the connection cannot
            # carry another request: it closes after the answer.
            await _refuse(send, 413, {"error": f"the request body is over {self.max_body_bytes} bytes"}, close=True)
            return
        try:
            too_deep = _nests_deeper_than(json.loads(body), _MAX_BODY_DEPTH)
        except RecursionError:
            too_deep = True
        except ValueError:
            await _refuse(send, 400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return
        if too_deep:
            await _refuse(send, 400, {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": f"Invalid Request: the body nests deeper than {_MAX_BODY_DEPTH} levels"}})
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._run(scope, replay, send)

    async def _read(self, headers: dict[str, str], receive: Receive) -> bytes:
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_body_bytes:
            raise _BodyTooLarge
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:
                raise _BodyTooLarge
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        return b"".join(chunks)

    async def _run(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False

        async def tracked(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        except Exception:  # noqa: BLE001 - reported to the client without its text
            logger.exception("A2A request failed")
            if not started:
                await _refuse(send, 500, {"error": "internal error"})


def _nests_deeper_than(value: Any, limit: int) -> bool:
    """Whether a parsed body nests its arrays and objects more than ``limit``
    deep, walked without recursion."""
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if not isinstance(item, (dict, list)):
            continue
        if depth > limit:
            return True
        pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return False


def _foreign_origin(origin: Optional[str], allowed: set[str], host: str) -> bool:
    """An Origin naming a host other than this server's own names; none at all
    is a client that is not a browser."""
    if origin is None:
        return False
    origin_host = urlsplit(origin).netloc.lower()
    return not origin_host or (origin_host not in allowed and origin_host != host)


async def _refuse(send: Send, status: int, body: Any, *, close: bool = False) -> None:
    data = json.dumps(body).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
    if close:
        headers.append((b"connection", b"close"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


def make_chap_a2a_app(
    coord: Coordinator,
    *,
    base_url: str,
    participant: Optional[str] = None,
    skill_filter: Optional[Callable[[str], bool]] = None,
    allowed_hosts: Iterable[str] = (),
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    version: Optional[str] = None,
) -> ChapA2aGuard:
    """The A2A server for ``coord``, behind :class:`ChapA2aGuard`.

    It serves the Agent Card at ``/.well-known/agent-card.json`` and JSON-RPC
    at ``/``, for A2A 1.0 clients and, with the SDK's v0.3 compatibility, for
    0.3 ones. ``base_url`` is the endpoint the card advertises. ``participant``
    and ``skill_filter`` are the executor's, and the card lists the same
    skills: the agent's, ``AGENT_MCP_TOOLS``, when ``skill_filter`` is unset.
    ``version`` is the card's, the package's when unset. Refusals are kept
    in a :class:`ChapTaskStore`.
    """
    card = make_chap_agent_card(base_url=base_url, skill_filter=skill_filter,
                                **({"version": version} if version is not None else {}))
    handler = DefaultRequestHandler(
        agent_executor=make_chap_agent_executor(coord, participant=participant, skill_filter=skill_filter),
        task_store=ChapTaskStore(),
        agent_card=card,
    )
    app = Starlette(routes=[
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True),
    ])
    return ChapA2aGuard(app, allowed_hosts=allowed_hosts, max_body_bytes=max_body_bytes)
