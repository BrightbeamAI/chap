"""
chap_coordinator.transports.a2a_server
=======================================

A2A server adapter for a CHAP Coordinator. Wraps a Coordinator
instance and exposes every CHAP method as an A2A skill. Remote
agents (Azure AI Foundry, Amazon Bedrock AgentCore, Google ADK, the
rest) can discover the Coordinator's capabilities via its Agent Card
and delegate work to it.

Spec target: A2A 1.0 (current stable). CHAP 0.3.

Usage::

    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_coordinator.transports.a2a_server import make_chap_agent_executor, make_chap_agent_card

    coord = Coordinator()
    executor = make_chap_agent_executor(coord)
    agent_card = make_chap_agent_card(base_url="http://localhost:9000")

    # Plumb into the a2a-sdk's request handler and ASGI app per the
    # reference at reference/a2a-server-py/server.py.

Architecture notes
------------------

- One Coordinator, one A2A agent. Multi-workspace is handled inside
  the Coordinator.
- The adapter holds no state. Each A2A ``message/send`` call
  translates to a JSON-RPC envelope and dispatches through
  ``coord.dispatch()``.
- Every CHAP method appears as a discrete ``AgentSkill`` on the
  Agent Card with id ``chap.<method>``. This matches the MCP
  transport's ``chap.<method>`` tool naming, so a caller fluent in
  one is fluent in the other.
- A2A messages carry the CHAP params in a data part. The skill id
  identifies which CHAP method to dispatch.
- An answer is an agent message carrying the result. A refusal is a task
  that has failed, in A2A's terms: rejected when the coordinator refused
  the call, failed when it could not complete it. Either way the status
  message carries the CHAP error, ``{chap_error, message, data?}``.
- ``participant`` pins ``from``, as it does for the MCP server, so a
  connection meant for one agent cannot act in anyone else's name.
- Authentication is intentionally out of scope at this layer.
  A2A's security schemes (OAuth, mTLS, API key) attach to the
  Agent Card and are enforced at the HTTP transport. Real
  deployments add those there.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from itertools import count
from typing import Any, Callable

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from chap_coordinator.coordinator import Coordinator

from .mcp_schemas import SCHEMAS, TOOL_NAMES, method_for_tool
from .mcp_tools import TOOL_DESCRIPTIONS


__all__ = [
    "ChapAgentExecutor",
    "CHAP_MEDIA_TYPE",
    "make_chap_agent_executor",
    "make_chap_agent_card",
    "dispatch_a2a_message",
]

logger = logging.getLogger(__name__)

#: JSON-RPC's internal error: a fault inside the coordinator, which refused nothing.
_INTERNAL_ERROR = -32603

#: What a client is told of a fault inside the server; the detail is in the server's log.
_FAILED_INSIDE = "The CHAP call failed inside the server; its log has the detail."

#: The media type of every part a CHAP skill takes and returns: the call's
#: params in, and its result or refusal out, as JSON data parts. A2A names
#: input and output modes by media type.
CHAP_MEDIA_TYPE = "application/json"


# A2A skill ids reuse the MCP tool naming. The only difference is
# how the args are carried on the wire: MCP puts them in
# ``tools/call`` arguments; A2A puts them in a ``DataPart`` on a
# Message. The CHAP envelope built from either path is identical.


def make_chap_agent_card(
    *,
    base_url: str,
    name: str = "CHAP Coordinator",
    description: str = (
        "Collaborative Human-Agent Protocol coordinator. "
        "Exposes every CHAP method as a discrete skill so remote agents "
        "can discover and drive workspaces, tasks, reviews, deliberations, "
        "handoffs, and audit operations."
    ),
    version: str = "0.3.4",
    skill_filter: Callable[[str], bool] | None = None,
) -> AgentCard:
    """Build the AgentCard advertised at ``/.well-known/agent-card.json``.

    Every CHAP method becomes an :class:`AgentSkill` with id
    ``chap.<method>`` and description tuned for LLM consumption.

    The card lists the JSON-RPC endpoint twice: at A2A 1.0, and at 0.3 for
    a client that has not moved, which the server answers where its
    JSON-RPC routes have ``enable_v0_3_compat`` set. The 0.3 entry is also
    what gives the served card the fields a 0.3 client reads, ``url`` among
    them. Both match the TypeScript card.
    """
    filter_fn = skill_filter or (lambda _name: True)

    skills: list[AgentSkill] = []
    for tool_name in TOOL_NAMES:
        if not filter_fn(tool_name):
            continue
        method = method_for_tool(tool_name)
        if method is None:
            continue
        skill = AgentSkill(
            id=tool_name,
            name=tool_name,
            description=TOOL_DESCRIPTIONS.get(tool_name, f"CHAP method {method}."),
            tags=["chap", method.split(".")[0]],
            input_modes=[CHAP_MEDIA_TYPE],
            output_modes=[CHAP_MEDIA_TYPE],
        )
        skills.append(skill)

    return AgentCard(
        name=name,
        description=description,
        version=version,
        default_input_modes=[CHAP_MEDIA_TYPE],
        default_output_modes=[CHAP_MEDIA_TYPE],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        skills=skills,
        supported_interfaces=[
            # "JSONRPC" is the value A2A defines, which a2a-sdk's
            # ClientFactory matches exactly. With "jsonrpc" every SDK client
            # given the card found no compatible transport.
            AgentInterface(url=base_url, protocol_binding="JSONRPC", protocol_version=version)
            for version in ("1.0", "0.3")
        ],
    )


def dispatch_a2a_message(
    coord: Coordinator,
    message: Message,
    *,
    envelope_id_factory: Callable[[], Any] | None = None,
    skill_filter: Callable[[str], bool] | None = None,
    participant: str | None = None,
) -> dict[str, Any]:
    """Translate an A2A Message into a CHAP envelope and dispatch it.

    The Message is expected to carry the CHAP method name in its
    ``metadata`` under key ``skill`` (which the A2A SDK populates from
    the requested skill id) and the params in the first data part.
    With ``participant`` the call is made as that member, and one that
    names another ``from`` is refused with -32011.

    Returns the CHAP response envelope.
    """
    counter = envelope_id_factory or _default_id_factory()
    skill_id, params = _extract_skill_and_params(message)
    method = method_for_tool(skill_id) if isinstance(skill_id, str) else None

    if method is None:
        return {
            "jsonrpc": "2.0",
            "id": counter(),
            "error": {
                "code": -32601,
                "message": (
                    f"Unknown CHAP skill: {skill_id!r}. "
                    "Expected an A2A skill id of the form 'chap.<method>'."
                ),
            },
        }
    if skill_filter is not None and not skill_filter(skill_id):
        return {
            "jsonrpc": "2.0",
            "id": counter(),
            "error": {
                "code": -32601,
                "message": f"CHAP skill not offered by this server: {skill_id}",
            },
        }

    args = dict(params or {})
    if participant is not None:
        if "from" in args and args["from"] != participant:
            return {
                "jsonrpc": "2.0",
                "id": counter(),
                "error": {
                    "code": -32011,
                    "message": (f"This server acts as {participant}; a call cannot name "
                                f"another from ({json.dumps(args['from'], ensure_ascii=False)})."),
                },
            }
        args["from"] = participant

    return coord.dispatch({
        "jsonrpc": "2.0",
        "id": counter(),
        "method": method,
        "params": args,
    })


def _default_id_factory() -> Callable[[], str]:
    c = count(1)
    return lambda: f"a2a-{next(c)}"


def _extract_skill_and_params(message: Message) -> tuple[str | None, dict[str, Any]]:
    """Pull the CHAP skill id and params dict out of an A2A Message.

    Discovery order for the skill id:
      1. ``message.metadata["skill"]`` (set by the A2A SDK when the
         client invokes a named skill).
      2. ``part.data["skill"]`` on the first DataPart.

    Discovery order for the params:
      1. ``part.data["params"]`` on the first DataPart.
      2. ``part.data`` itself (treating the entire data blob as
         params, minus the ``skill`` key).
    """
    skill_id: str | None = None
    params: dict[str, Any] = {}

    metadata = MessageToDict(message.metadata) if len(message.metadata.fields) else {}
    skill_id = metadata.get("skill")

    for part in message.parts:
        if part.WhichOneof("content") != "data":
            continue
        blob = MessageToDict(part.data) if part.data else {}
        if not isinstance(blob, dict):
            continue
        if skill_id is None:
            skill_id = blob.get("skill")
        if "params" in blob and isinstance(blob["params"], dict):
            params = blob["params"]
        else:
            params = {k: v for k, v in blob.items() if k != "skill"}
        break

    return skill_id, params


class ChapAgentExecutor(AgentExecutor):
    """A2A AgentExecutor that dispatches messages to a CHAP Coordinator.

    Plugs into the a2a-sdk's ``DefaultRequestHandler`` pattern.
    """

    def __init__(
        self,
        coord: Coordinator,
        *,
        envelope_id_factory: Callable[[], Any] | None = None,
        skill_filter: Callable[[str], bool] | None = None,
        participant: str | None = None,
    ) -> None:
        self._coord = coord
        self._next_id = envelope_id_factory or _default_id_factory()
        # The card only lists skills; the executor is what runs them, so it
        # takes the same filter as the card and refuses what it excludes.
        self._skill_filter = skill_filter
        self._participant = participant

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            response = dispatch_a2a_message(
                self._coord, context.message, envelope_id_factory=self._next_id,
                skill_filter=self._skill_filter, participant=self._participant,
            )
        except Exception:  # noqa: BLE001 - a fault in the server, reported without its text
            # The exception's text can carry a path or a stack line, so it
            # goes to the server's log, which the client never sees.
            logger.exception("CHAP dispatch failed")
            response = {"jsonrpc": "2.0", "error": {"code": _INTERNAL_ERROR, "message": _FAILED_INSIDE}}
        else:
            if response.get("error", {}).get("code") == _INTERNAL_ERROR:
                # A fault inside the coordinator, whose data can carry the
                # exception's text. It goes to the server's log, and the
                # client is told only that the call failed. A refusal is
                # passed on as it came.
                logger.error("CHAP call failed: %s", response["error"])
                response = {"jsonrpc": "2.0", "error": {"code": _INTERNAL_ERROR, "message": _FAILED_INSIDE}}

        if "error" not in response:
            await event_queue.enqueue_event(self._agent_message(context, response.get("result")))
            return

        err = response["error"]
        payload: dict[str, Any] = {"chap_error": err.get("code"), "message": err.get("message", "")}
        if "data" in err:
            payload["data"] = err["data"]
        task_id = context.task_id or ""
        await event_queue.enqueue_event(Task(
            id=task_id,
            context_id=context.context_id or "",
            status=TaskStatus(
                state=(TaskState.TASK_STATE_FAILED if err.get("code") == _INTERNAL_ERROR
                       else TaskState.TASK_STATE_REJECTED),
                message=self._agent_message(context, payload, task_id=task_id),
                timestamp=datetime.now(timezone.utc),
            ),
            history=[context.message] if context.message else [],
        ))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """A CHAP call takes effect within ``execute``, before it returns, so no
        call is ever left running to stop, and there is nothing to cancel."""

    def _agent_message(self, context: RequestContext, payload: Any, *, task_id: str = "") -> Message:
        """The agent's message: one JSON data part. An answer is a message
        alone, with no task made for it, so it names none; a refusal names
        its task."""
        value = Value()
        ParseDict(payload if isinstance(payload, dict) else {"result": payload}, value)
        return Message(
            message_id=f"chap-resp-{self._next_id()}",
            role=Role.ROLE_AGENT,
            context_id=context.context_id or "",
            task_id=task_id,
            parts=[Part(data=value, media_type=CHAP_MEDIA_TYPE)],
        )


def make_chap_agent_executor(
    coord: Coordinator,
    *,
    envelope_id_factory: Callable[[], Any] | None = None,
    skill_filter: Callable[[str], bool] | None = None,
    participant: str | None = None,
) -> ChapAgentExecutor:
    """Convenience constructor mirroring ``make_chap_mcp_server``.

    Pass the same ``skill_filter`` as :func:`make_chap_agent_card`: the card
    only lists skills, and the executor refuses the ones the filter excludes.
    ``participant`` makes every call as that member: ``from`` is filled in,
    and a call that names another is refused with -32011. Use it where the
    server is one participant's connection, such as an orchestrator's.
    Default: each call names its own ``from``.
    """
    return ChapAgentExecutor(coord, envelope_id_factory=envelope_id_factory,
                             skill_filter=skill_filter, participant=participant)
