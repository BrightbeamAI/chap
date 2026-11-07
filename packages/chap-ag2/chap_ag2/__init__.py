"""
chap-ag2: put an AG2 (AutoGen) agent's message, or its tool call, in front
of a person, as a CHAP review, and carry on with what they decide.

AG2 0.x asks for human input through ``get_human_input`` on a
UserProxyAgent or ConversableAgent running with ``human_input_mode="ALWAYS"``.
At that turn the message under review is the agent's last message, its text
and the tool calls it would have run, and the reply is the human's.
request_reply opens a review of the message, waits for the person's
decision, made at a CHAP desk, and returns the reply that carries it out:

    CHAP decision                          reply to AG2
    -----------------------------------    ---------------------------------
    decide.approve                         "" (AG2 carries on: it runs the
                                           message's tool calls as proposed)
    decide.override of a text message      the person's version, as the reply
    decide.override of a tool call         a reply that refuses the call, and
                                           gives the person's version of it
    decide.reject, asking for another      the person's note
    any other ending                       raises ReviewRejected

AG2 1.x asks for human input through its own hook (``ag2.hitl``), and its
``approval_required()`` tool middleware asks a yes or no through it.
tool_approval is a tool middleware of the same kind: it puts each call of a
tool in front of a person at a CHAP desk, where they can also change its
arguments, and runs it as they decide.

record_turn records a reply your own code took, in the reviewer's name,
marked as recorded, in a workspace where nobody signs.

The bridge depends on chap-coordinator alone: AG2 is imported only by the
1.x middleware, to build the result of a call it does not run, and messages
and replies are read as plain values.
"""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from chap_coordinator.bridge import (
    ChapError, Decision, PersonRequired, ReviewClient, ReviewRejected, decided_arguments, json_patch,
)

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-ag2")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"

#: The name chap-ag2 0.3.4 gave the coordinator's refusals.
ChapBridgeError = ChapError

_SENT_BACK = ("The reviewer asked for another proposal, so the agent's next one is reviewed on a task "
              "of its own.")


def _arguments(text: Any) -> Any:
    """A tool call's arguments, JSON text in AG2 0.x, as the object they
    name, so the person reads them; text that is not JSON stays text."""
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text) if text.strip() else {}
    except ValueError:
        return text


def _proposal(message: Any) -> Any:
    """What an AG2 0.x message puts to the person: its text, and every tool
    or function call it would have run, with their arguments. A message with
    no calls is its text alone."""
    if not isinstance(message, dict):
        return message
    calls = [(call.get("id"), call.get("function") or {}) for call in message.get("tool_calls") or []]
    if message.get("function_call"):
        calls.append((None, message["function_call"]))
    if not calls:
        return message.get("content")
    return {"content": message.get("content"),
            "tool_calls": [{"id": call_id, "name": function.get("name"),
                            "arguments": _arguments(function.get("arguments"))}
                           for call_id, function in calls]}


def _makes_calls(proposal: Any) -> bool:
    return isinstance(proposal, dict) and "tool_calls" in proposal


def _is_empty(reply: Any) -> bool:
    return not reply.strip() if isinstance(reply, str) else not reply


def _reply(proposal: Any, decision: Decision) -> str:
    """The human's reply that carries a decision out in AG2 0.x."""
    if decision.kind == "approve" and decision.approved:
        return ""
    if decision.approved and _makes_calls(proposal):
        decided = decision.output["tool_calls"] if _makes_calls(decision.output) else decision.output
        return f"The reviewer changed this call, so it was not made. Make it as they set it: {json.dumps(decided)}"
    if decision.approved:
        return decision.output if isinstance(decision.output, str) else json.dumps(decision.output)
    if decision.revision_requested:
        return decision.comment or "The reviewer asked for another draft."
    raise ReviewRejected.from_decision(decision)


def _why_not(decision: Decision) -> str:
    """What the model is told about a call the reviewer did not let run."""
    if decision.approved:
        return ("The reviewer's edit changed the call itself, or took its arguments away, which this "
                "run cannot carry out, so the call was not made.")
    if decision.comment:
        return decision.comment
    if decision.outcome in ("reject", "revise"):
        return "The reviewer rejected this call."
    return f"The review ended without an approval ({decision.outcome}), so the call was not made."


@dataclass
class ChapTurnBridge:
    """The agent's side of the reviews in one workspace. Share it across a
    conversation.

    ``coord`` is a Coordinator in this process, or a desk's, through
    ``chap_coordinator.client``: an HttpCoordinator, or a Participant for the
    agent, which signs the agent's calls with its key. The bridge joins the
    agent when it is made. A review is addressed to every person in the
    workspace; ``reviewer`` names the person a recorded decision is made in
    by default, who joins when the first review opens where nobody signs.
    Where people sign, each joins from the desk with their own key, and the
    bridge joins nobody in their name.

    ``on_envelope`` sees each call's method and params before it is sent.
    ``client`` reads a task, for a coordinator that cannot: anything whose
    ``task(task_id)`` returns the ``GET /api/tasks/<id>`` view.
    """

    coord:     Any
    workspace: str
    agent:     str
    reviewer:  str
    profiles:  tuple = ("core/1.0", "review/1.0")
    on_envelope: Optional[Callable[[str, dict], None]] = None
    client: Any = None
    _review: ReviewClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._review = ReviewClient(
            self.coord, self.workspace, self.agent, self.reviewer, profiles=self.profiles,
            client=self.client, on_envelope=self._seen, name="chap-ag2")

    def _seen(self, method: str, params: dict) -> None:
        if self.on_envelope is not None:
            self.on_envelope(method, params)

    # ---- AG2 0.x: a human-input turn ----------------------------------

    def request_reply(self, message: Any, *, timeout: Optional[float] = None,
                      poll_seconds: float = 2.0) -> str:
        """Open a review of the agent's message, wait for the person's
        decision, and return the reply for get_human_input to give AG2.

        The message is reviewed whole: its text, and every tool or function
        call it would have run, with their arguments. A message with neither
        is refused with ValueError, since nobody can approve what they were
        not shown. The review is on a task that requires review, so only a
        person's decision completes it.

        An approval returns "", and AG2 carries on with its auto-reply,
        which runs the message's calls as proposed. An override of a text
        message returns the person's version, which AG2 sends the agent as
        the human's reply. AG2 0.x runs a tool call only with the arguments
        the model wrote, so an override of a tool call returns a reply that
        refuses the call and gives the person's version, for the agent to
        make again. A rejection that asks for another proposal returns the
        person's note, and its task is declined: the agent's next message is
        reviewed on a task of its own. Anything else raises ReviewRejected,
        which ends the chat, so nothing takes its last message for an
        approved one.

        The task is read every ``poll_seconds``. After ``timeout`` seconds
        the review is withdrawn, so no decision lands on it later, and
        TimeoutError raised; None waits as long as the reviewer takes.
        """
        proposal = _proposal(message)
        decision = self._review.review(proposal, kind="agent_message", input=proposal,
                                       timeout=timeout, poll_seconds=poll_seconds)
        if decision.revision_requested:
            self._review.close(decision.task_id, _SENT_BACK)
        return _reply(proposal, decision)

    async def request_reply_async(self, message: Any, *, timeout: Optional[float] = None,
                                  poll_seconds: float = 2.0) -> str:
        """:meth:`request_reply` for ``a_get_human_input``, which AG2 asks in
        a chat started with ``a_initiate_chat``. The event loop is never held
        up, and cancelling the awaiting task withdraws the review."""
        proposal = _proposal(message)
        decision = await self._review.review_async(proposal, kind="agent_message", input=proposal,
                                                   timeout=timeout, poll_seconds=poll_seconds)
        if decision.revision_requested:
            await asyncio.to_thread(self._review.close, decision.task_id, _SENT_BACK)
        return _reply(proposal, decision)

    def wait_for_decision(self, task_id: str, *, timeout: Optional[float] = None,
                          poll_seconds: float = 2.0) -> Decision:
        """Wait for the decision on a review this bridge opened, and return
        it. After ``timeout`` seconds the review is withdrawn and
        TimeoutError raised; None waits as long as the reviewer takes."""
        return self._review.wait(task_id, timeout=timeout, poll_seconds=poll_seconds)

    # ---- AG2 1.x: a tool call -------------------------------------------

    def tool_approval(self, *, timeout: Optional[float] = None,
                      poll_seconds: float = 2.0) -> Callable:
        """A tool middleware for AG2 1.x that puts each call of the tool in
        front of a person and runs it as they decide:

            @tool(middleware=[bridge.tool_approval()])
            def transfer(amount: int, to: str) -> str: ...

        The call, its tool name, arguments and call id, is the artefact under
        review, on a task that requires review, so only a person's decision
        completes it. An approval runs the call as the model made it, and an
        override of its arguments runs it with the person's. Anything else
        runs nothing and answers the model with the person's note: a
        rejection, an abstention, an edit to anything but the arguments, or a
        revision, whose task is then declined, as the model's next call is
        reviewed on a task of its own.

        The task is read every ``poll_seconds``, and the event loop is never
        held up. A call nobody decided within ``timeout`` seconds runs
        nothing, the model is told so, and the review is withdrawn, so no
        decision lands on it later; None waits as long as the reviewer
        takes. A cancelled turn withdraws the review too.
        """
        async def middleware(call_next: Any, event: Any, context: Any) -> Any:
            from ag2.events import ToolResultEvent

            proposed = {"tool": event.name, "args": json.loads(event.arguments or "{}"),
                        "tool_call_id": event.id}
            try:
                decision = await self._review.review_async(
                    proposed, kind=event.name or "tool_call", input=proposed,
                    timeout=timeout, poll_seconds=poll_seconds)
            except TimeoutError:
                return ToolResultEvent.from_call(
                    event, result=f"Nobody decided on this call within {timeout} seconds, so it was not made.")
            args = decided_arguments(decision, proposed)
            if args is not None:
                if args != proposed["args"]:
                    event = copy.copy(event)
                    event.arguments = json.dumps(args)
                    event.serialized_arguments = args
                return await call_next(event, context)
            if decision.revision_requested:
                await asyncio.to_thread(self._review.close, decision.task_id, _SENT_BACK)
            return ToolResultEvent.from_call(event, result=_why_not(decision))

        return middleware

    # ---- record a decision made elsewhere --------------------------

    def record_turn(self, message: Any, reply: Any, *,
                    decision: Optional[str] = None,
                    approver: Optional[str] = None,
                    rationale: Optional[str] = None,
                    tags: Optional[list[str]] = None,
                    intent_preserved: Optional[bool] = None) -> Optional[str]:
        """Record one human turn your own code took, and return the task id,
        or None where the turn is not a decision.

        ``message`` is the agent message the person answered, and ``reply``
        their reply. ``decision`` is approve, override or reject. Without
        it, only an empty reply is taken for one, an approval; any other
        reply records nothing, since AG2's loop carries plain dialogue too.
        An override of a tool call is refused with ValueError: AG2 0.x runs
        a call only with the arguments the model wrote, so the edit would be
        on the log and never run.

        The decision is recorded in the approver's name, a person's
        ``human:`` URI (the bridge's reviewer by default), bound to the
        digest of the message and marked ``via: "bridge-record"``. Where
        people sign, PersonRequired is raised and nothing is recorded: they
        decide at the desk, through request_reply.
        """
        if decision is None:
            if not _is_empty(reply):
                return None
            decision = "approve"
        if decision not in ("approve", "override", "reject"):
            raise ValueError(f"decision must be 'approve', 'override', or 'reject': {decision!r}")
        proposal = _proposal(message)
        record = dict(task_kind="agent_message", input=proposal, reviewer=approver, tags=tags)
        if decision == "override":
            if _makes_calls(proposal):
                raise ValueError("AG2 0.x runs a tool call only with the arguments the model wrote, so an "
                                 "edit of one is not recorded: it would never run")
            diff = json_patch(proposal, reply)
            if diff:
                return self._review.record(
                    proposal, "override", comment=rationale or "reviewer edited the message", diff=diff,
                    intent_preserved=True if intent_preserved is None else intent_preserved, **record).task_id
            decision = "approve"
        if decision == "approve":
            return self._review.record(proposal, "approve", comment=rationale, **record).task_id
        note = rationale or (reply if isinstance(reply, str) and not _is_empty(reply) else None)
        return self._review.record(proposal, "reject", comment=note, **record).task_id

    def audit(self) -> list[dict]:
        """The workspace's audit log."""
        return self._review.audit()


__all__ = [
    "ChapBridgeError",
    "ChapError",
    "ChapTurnBridge",
    "Decision",
    "PersonRequired",
    "ReviewRejected",
]
