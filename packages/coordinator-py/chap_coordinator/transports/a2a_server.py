"""
chap_coordinator.transports.a2a_server
=======================================

A2A server adapter for a CHAP Coordinator. Wraps a Coordinator
instance and exposes CHAP methods as A2A skills: by default the agent's,
the ``AGENT_MCP_TOOLS`` set, and every method on request. Remote agents
(Azure AI Foundry, Amazon Bedrock AgentCore, Google ADK, the rest) can
discover the Coordinator's capabilities via its Agent Card and delegate
work to it.

Spec target: A2A 1.0 (current stable). CHAP 0.3.

Usage::

    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_coordinator.transports.a2a_server import make_chap_agent_executor, make_chap_agent_card

    coord = Coordinator()
    executor = make_chap_agent_executor(coord, participant="agent:orchestrator@local")
    agent_card = make_chap_agent_card(base_url="http://localhost:9000")

    # chap_coordinator.transports.a2a_app.make_chap_a2a_app builds the
    # a2a-sdk request handler and routes from these, behind the checks a
    # server on the user's machine needs; python -m
    # chap_coordinator.transports.a2a_cli serves it.

Architecture notes
------------------

- One Coordinator, one A2A agent. Multi-workspace is handled inside
  the Coordinator.
- The adapter holds no state. Each A2A ``message/send`` call
  translates to a JSON-RPC envelope and dispatches through
  ``coord.dispatch()``.
- Each CHAP method offered appears as a discrete ``AgentSkill`` on the
  Agent Card with id ``chap.<method>``. This matches the MCP
  transport's ``chap.<method>`` tool naming, so a caller fluent in
  one is fluent in the other.
- A2A messages carry the CHAP params in a data part. The skill id
  identifies which CHAP method to dispatch.
- An answer is an agent message carrying the result. A refusal is a task
  that has failed, in A2A's terms: rejected when the coordinator refused
  the call, failed when it could not complete it. Either way the status
  message carries the CHAP error, ``{chap_error, message, data?}``.
- a2a-sdk holds a data part's numbers as doubles. A call's params reach
  the coordinator with whole numbers as ints, and
  :func:`~chap_coordinator.transports.a2a_app.make_chap_a2a_app` sends
  them to the client as integers, as the TypeScript server does; an app
  that serves the executor through the SDK's routes alone sends 1042 as
  1042.0.
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
from collections import OrderedDict
from datetime import datetime, timezone
from itertools import count
from typing import Any, Callable

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.tasks import InMemoryTaskStore
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
from .mcp_tools import AGENT_MCP_TOOLS, TOOL_DESCRIPTIONS


__all__ = [
    "ChapAgentExecutor",
    "ChapTaskStore",
    "CHAP_MEDIA_TYPE",
    "DEFAULT_MAX_TASKS",
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

#: How many tasks a :class:`ChapTaskStore` keeps by default.
DEFAULT_MAX_TASKS = 1000


def _agent_skills(skill_id: str) -> bool:
    """The skills an agent's own connection offers, the MCP server's ``AGENT_MCP_TOOLS``."""
    return skill_id in AGENT_MCP_TOOLS


def _whole_numbers(value: Any) -> Any:
    """``value`` with each float that holds a whole number made an int.

    a2a-sdk carries a data part as ``google.protobuf.Value``, which holds
    every number as a double, so 1042 comes out of it as 1042.0. JSON does
    not tell the two apart, and the TypeScript server sends 1042, so a whole
    number within the range a double holds exactly, 2**53, is an int again.
    """
    if isinstance(value, float) and value.is_integer() and abs(value) <= 2 ** 53:
        return int(value)
    if isinstance(value, dict):
        return {key: _whole_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_whole_numbers(item) for item in value]
    return value


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
        "Each CHAP method it offers is a discrete skill, so remote agents "
        "can discover and drive the work in its workspaces: tasks, reviews "
        "and the audit log."
    ),
    version: str = "0.3.5",
    skill_filter: Callable[[str], bool] | None = None,
    legacy_compat: bool = True,
) -> AgentCard:
    """Build the AgentCard advertised at ``/.well-known/agent-card.json``.

    Each CHAP method ``skill_filter`` lets through becomes an
    :class:`AgentSkill` with id ``chap.<method>`` and description tuned for
    LLM consumption. Unset, the filter is the agent's, ``AGENT_MCP_TOOLS``;
    ``lambda _name: True`` lists every method. Pass the same filter as the
    executor's.

    The card lists the JSON-RPC endpoint at A2A 1.0, and with
    ``legacy_compat``, the default, at 0.3 as well, for a client that has
    not moved: the server answers one where its JSON-RPC routes have
    ``enable_v0_3_compat`` set, as :func:`make_chap_a2a_app`'s do. Pass
    ``legacy_compat=False`` for an app whose routes do not, so the card
    claims only what the server answers. The 0.3 entry is also what gives
    the served card the fields a 0.3 client reads, ``url`` among them. Both
    match the TypeScript card.
    """
    filter_fn = skill_filter or _agent_skills

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
            # ClientFactory matches exactly.
            AgentInterface(url=base_url, protocol_binding="JSONRPC", protocol_version=protocol_version)
            for protocol_version in (("1.0", "0.3") if legacy_compat else ("1.0",))
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
    names another ``from`` is refused with -32011. A ``human:`` participant
    is refused with ``ValueError``. With ``skill_filter`` a skill it excludes
    is refused with -32601; without one every skill runs.

    Returns the CHAP response envelope.
    """
    _refuse_person(participant)
    counter = envelope_id_factory or _default_id_factory()
    skill_id, params = _extract_skill_and_params(message)
    method = method_for_tool(skill_id) if skill_id is not None else None

    if method is None:
        return {
            "jsonrpc": "2.0",
            "id": counter(),
            "error": {
                "code": -32601,
                "message": (
                    f"Unknown CHAP skill: {skill_id if skill_id is not None else '(missing)'}. "
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


def _refuse_person(participant: str | None) -> None:
    """Refuse a ``human:`` participant, as the commands refuse one: a server
    that makes every call as one member is a connection for an agent, and a
    person decides in a desk."""
    if participant is not None and participant.startswith("human:"):
        raise ValueError(f"participant {participant} names a person. This server acts for an agent, "
                         "and a person decides in a desk; give the agent's own URI, such as "
                         "agent:orchestrator.")


def _default_id_factory() -> Callable[[], str]:
    c = count(1)
    return lambda: f"a2a-{next(c)}"


def _extract_skill_and_params(message: Message) -> tuple[str | None, dict[str, Any]]:
    """Pull the CHAP skill id and params dict out of an A2A Message.

    Discovery order for the skill id, which counts only as a string:
      1. ``message.metadata["skill"]`` (set by the A2A SDK when the
         client invokes a named skill).
      2. ``part.data["skill"]`` on the first DataPart.

    Discovery order for the params:
      1. ``part.data["params"]`` on the first DataPart.
      2. ``part.data`` itself (treating the entire data blob as
         params, minus the ``skill`` key).

    A first DataPart that holds no JSON object gives neither. The
    TypeScript executor reads a message the same way.
    """
    skill_id: str | None = None
    params: dict[str, Any] = {}

    metadata = _whole_numbers(MessageToDict(message.metadata)) if len(message.metadata.fields) else {}
    if isinstance(metadata.get("skill"), str):
        skill_id = metadata["skill"]

    for part in message.parts:
        if part.WhichOneof("content") != "data":
            continue
        blob = _whole_numbers(MessageToDict(part.data)) if part.data else {}
        if not isinstance(blob, dict):
            break
        if skill_id is None and isinstance(blob.get("skill"), str):
            skill_id = blob["skill"]
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
        _refuse_person(participant)
        self._coord = coord
        self._next_id = envelope_id_factory or _default_id_factory()
        # The card only lists skills; the executor is what runs them, so it
        # takes the same filter as the card and refuses what it excludes.
        self._skill_filter = skill_filter or _agent_skills
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


class ChapTaskStore(InMemoryTaskStore):
    """The task store a CHAP agent needs.

    The executor answers a call with a message, and makes a task only to
    refuse one, a task that is final the moment it is made. The SDK's
    ``InMemoryTaskStore`` keeps every task for as long as the server runs,
    each with the request in its history, so every refused call, up to the
    1 MiB a request may carry, stayed in memory.

    This store keeps the ``max_tasks`` most recently saved tasks, 1,000 by
    default, and keeps each without the client's own messages. GetTask still
    answers for a recent refusal, with its state and the agent's message
    that carries the CHAP error, and memory stays bounded however many calls
    are refused. Tasks are scoped by caller, as ``InMemoryTaskStore`` scopes
    them. Mirrors ``ChapTaskStore`` in the TypeScript package.
    """

    def __init__(self, max_tasks: int = DEFAULT_MAX_TASKS) -> None:
        if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks < 1:
            raise ValueError(f"max_tasks takes a whole number of tasks, 1 or more; {max_tasks!r} is not one")
        super().__init__()
        self._max_tasks = max_tasks
        # Oldest first, each with the context it was saved under, which says
        # whose it is when it is dropped.
        self._order: OrderedDict[tuple[str, str], ServerCallContext] = OrderedDict()

    async def save(self, task: Task, context: ServerCallContext) -> None:
        kept = Task()
        kept.CopyFrom(task)
        del kept.history[:]
        kept.history.extend(message for message in task.history if message.role != Role.ROLE_USER)
        await super().save(kept, context)
        # Saved again, a task becomes the newest.
        key = (resolve_user_scope(context), task.id)
        self._order.pop(key, None)
        self._order[key] = context
        while len(self._order) > self._max_tasks:
            (_, task_id), owner = self._order.popitem(last=False)
            await super().delete(task_id, owner)


def make_chap_agent_executor(
    coord: Coordinator,
    *,
    envelope_id_factory: Callable[[], Any] | None = None,
    skill_filter: Callable[[str], bool] | None = None,
    participant: str | None = None,
) -> ChapAgentExecutor:
    """Convenience constructor mirroring ``make_chap_mcp_server``.

    ``skill_filter`` names the skills the executor runs; one it excludes is
    refused with -32601. Unset, it is the agent's, ``AGENT_MCP_TOOLS``, so a
    connection made for an agent cannot pause the workspace, change its
    members or decide who reviews; ``lambda _name: True`` runs every method.
    Pass the same filter as :func:`make_chap_agent_card`: the card only
    lists skills, and the executor refuses the ones the filter excludes.
    ``participant`` makes every call as that member: ``from`` is filled in,
    and a call that names another is refused with -32011. Use it where the
    server is one participant's connection, such as an orchestrator's. A
    ``human:`` URI is refused with ``ValueError``: a person decides in a
    desk. Default: each call names its own ``from``.
    """
    return ChapAgentExecutor(coord, envelope_id_factory=envelope_id_factory,
                             skill_filter=skill_filter, participant=participant)
