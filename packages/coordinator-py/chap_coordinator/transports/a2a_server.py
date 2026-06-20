"""
chap_coordinator.transports.a2a_server
=======================================

A2A server adapter for a CHAP Coordinator. Wraps a Coordinator
instance and exposes every CHAP method as an A2A skill. Remote
agents (Azure AI Foundry, Amazon Bedrock AgentCore, Google ADK, the
rest) can discover the Coordinator's capabilities via its Agent Card
and delegate work to it.

Spec target: A2A 1.0 (current stable). CHAP 0.2.

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
- A2A messages carry the CHAP params in a ``DataPart``. The skill id
  identifies which CHAP method to dispatch.
- Authentication is intentionally out of scope at this layer.
  A2A's security schemes (OAuth, mTLS, API key) attach to the
  Agent Card and are enforced at the HTTP transport. Real
  deployments add those there.
"""
from __future__ import annotations

import json
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
    TaskState,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from chap_coordinator.coordinator import Coordinator

from .mcp_schemas import SCHEMAS, TOOL_NAMES, method_for_tool
from .mcp_tools import TOOL_DESCRIPTIONS


__all__ = [
    "ChapAgentExecutor",
    "make_chap_agent_executor",
    "make_chap_agent_card",
    "dispatch_a2a_message",
]


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
    version: str = "0.2.3",
    skill_filter: Callable[[str], bool] | None = None,
) -> AgentCard:
    """Build the AgentCard advertised at ``/.well-known/agent-card.json``.

    Every CHAP method becomes an :class:`AgentSkill` with id
    ``chap.<method>`` and description tuned for LLM consumption.
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
            input_modes=["data"],
            output_modes=["data"],
        )
        skills.append(skill)

    return AgentCard(
        name=name,
        description=description,
        version=version,
        default_input_modes=["data"],
        default_output_modes=["data"],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        skills=skills,
        supported_interfaces=[
            AgentInterface(
                url=base_url,
                protocol_binding="jsonrpc",
                protocol_version="1.0",
            ),
        ],
    )


def dispatch_a2a_message(
    coord: Coordinator,
    message: Message,
    *,
    envelope_id_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Translate an A2A Message into a CHAP envelope and dispatch it.

    The Message is expected to carry the CHAP method name in its
    ``metadata`` under key ``skill`` (which the A2A SDK populates from
    the requested skill id) and the params in the first ``DataPart``.

    Returns the CHAP response envelope.
    """
    counter = envelope_id_factory or _default_id_factory()
    skill_id, params = _extract_skill_and_params(message)
    method = method_for_tool(skill_id) if skill_id else None

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

    return coord.dispatch({
        "jsonrpc": "2.0",
        "id": counter(),
        "method": method,
        "params": params or {},
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
    ) -> None:
        self._coord = coord
        self._next_id = envelope_id_factory or _default_id_factory()
        self._cancelled: set[str] = set()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id and context.task_id in self._cancelled:
            await self._publish_text(
                event_queue, context, "Task was cancelled before execution.",
            )
            return

        try:
            response = dispatch_a2a_message(
                self._coord, context.message, envelope_id_factory=self._next_id,
            )
        except Exception as exc:  # noqa: BLE001
            await self._publish_text(
                event_queue, context,
                f"CHAP dispatch threw: {exc!r}",
            )
            return

        if "error" in response:
            err = response["error"]
            payload = {
                "chap_error": err.get("code"),
                "message": err.get("message", ""),
            }
            if "data" in err:
                payload["data"] = err["data"]
            await self._publish_data(event_queue, context, payload, is_error=True)
            return

        await self._publish_data(event_queue, context, response.get("result"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.task_id:
            self._cancelled.add(context.task_id)

    async def _publish_data(
        self,
        event_queue: EventQueue,
        context: RequestContext,
        payload: Any,
        *,
        is_error: bool = False,
    ) -> None:
        value = Value()
        ParseDict(payload if isinstance(payload, dict) else {"result": payload}, value)
        part = Part(data=value)
        if is_error:
            # Mark error responses via metadata for clients that care.
            from google.protobuf.struct_pb2 import Struct
            md = Struct()
            ParseDict({"is_error": True}, md)
            part.metadata.CopyFrom(md)
        msg = Message(
            message_id=f"chap-resp-{self._next_id()}",
            role=Role.ROLE_AGENT,
            context_id=context.context_id or "",
            task_id=context.task_id or "",
            parts=[part],
        )
        await event_queue.enqueue_event(msg)

    async def _publish_text(
        self, event_queue: EventQueue, context: RequestContext, text: str,
    ) -> None:
        msg = Message(
            message_id=f"chap-resp-{self._next_id()}",
            role=Role.ROLE_AGENT,
            context_id=context.context_id or "",
            task_id=context.task_id or "",
            parts=[Part(text=text)],
        )
        await event_queue.enqueue_event(msg)


def make_chap_agent_executor(
    coord: Coordinator,
    *,
    envelope_id_factory: Callable[[], Any] | None = None,
) -> ChapAgentExecutor:
    """Convenience constructor mirroring ``make_chap_mcp_server``."""
    return ChapAgentExecutor(coord, envelope_id_factory=envelope_id_factory)
