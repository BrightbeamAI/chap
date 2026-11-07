"""
chap-google-adk: put a Google ADK tool call that needs confirmation in front
of a person, as a CHAP review, and resume the run with what they decide.

ADK pauses a call that needs confirmation, from a
``FunctionTool(fn, require_confirmation=True)`` or a tool that calls
``tool_context.request_confirmation(hint=..., payload=...)``, with an
``adk_request_confirmation`` call, and resumes it with the ToolConfirmation
the app sends back. request_confirmation opens a review of the paused call,
waits for the person's decision, made at a CHAP desk, and returns the
ToolConfirmation that carries it out:

    CHAP decision                         ToolConfirmation
    ---------------------------------     -----------------------------------
    decide.approve                        confirmed=True
    decide.override of the arguments      confirmed=True, payload={"args": ...}
    decide.override of a tool's payload   confirmed=True, payload=the person's
    anything else, or no decision         confirmed=False

ADK runs a confirmed call with the arguments the model proposed. An edit of
them reaches the tool through ``apply_reviewed_args``, a
before_tool_callback that runs the call with the arguments the payload
carries; the bridge lets an edit run, or records one, only where the agent
has that callback and the tool takes the edited arguments. A tool that asks
for a payload of its own, as a leave tool asks for ``approved_days``, has
the payload reviewed with the call, and reads the person's version.

record_decision records a confirmation your own code made, in the
reviewer's name, marked as recorded, where nobody signs, and refuses one
ADK would not carry out as recorded.

The bridge depends on chap-coordinator alone: google-adk is imported only
by request_confirmation, to build the confirmation it returns, and calls,
confirmations and agents are read by their attributes.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from chap_coordinator import PatchError, apply_json_patch
from chap_coordinator.bridge import ChapError, Decision, PersonRequired, ReviewClient, json_patch

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-google-adk")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"

#: The name chap-google-adk 0.3.4 gave the coordinator's refusals.
ChapBridgeError = ChapError

_SENT_BACK = ("The reviewer asked for another call, so the model's next call is reviewed on a task "
              "of its own.")
_NOT_THE_CALL = ("The reviewer's edit changed the call itself, or took its arguments away, which this run "
                 "cannot carry out, so the call was not made.")


# ---- reading ADK objects without importing them ----------------------


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    """A field of an ADK model, or of the plain dict ADK serialises it into."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _paused(call: Any) -> tuple[Any, Optional[dict]]:
    """The call a confirmation pauses, and the confirmation it asks for: the
    originalFunctionCall and toolConfirmation of an adk_request_confirmation
    call, or a call given on its own, which asks for nothing more."""
    args = _attr(call, "args")
    if isinstance(args, dict) and "originalFunctionCall" in args:
        asked = args.get("toolConfirmation")
        return args["originalFunctionCall"], asked if isinstance(asked, dict) else None
    return call, None


def _artefact(original: Any, asked: Optional[dict]) -> dict:
    """What the person is shown: the call, and where the tool asks for a
    payload of its own, its question and the payload to fill in."""
    args = _attr(original, "args")
    artefact = {"tool": _attr(original, "name"), "args": dict(args) if args else {},
                "tool_call_id": _attr(original, "id")}
    if asked is not None and asked.get("payload") is not None:
        artefact["hint"] = asked.get("hint") or ""
        artefact["payload"] = asked["payload"]
    return artefact


def apply_reviewed_args(tool: Any, args: dict, tool_context: Any) -> None:
    """An ADK before_tool_callback that runs a confirmed call with the
    reviewer's arguments:

        LlmAgent(..., before_tool_callback=apply_reviewed_args)

    ADK runs a confirmed call with the arguments the model proposed. When the
    ToolConfirmation that resumed the call is confirmed and its payload
    carries ``args``, this replaces the call's arguments with those, before
    ADK checks them and calls the tool. A call with no such payload runs as
    proposed. It returns None, so the tool runs.
    """
    confirmation = getattr(tool_context, "tool_confirmation", None)
    payload = _attr(confirmation, "payload") if confirmation is not None else None
    edited = payload.get("args") if isinstance(payload, dict) else None
    if isinstance(edited, dict) and _attr(confirmation, "confirmed", False):
        args.clear()
        args.update(edited)
    return None


def _runs_edits(llm_agent: Any) -> bool:
    """Whether the agent runs a call with the arguments its confirmation carries."""
    callbacks = getattr(llm_agent, "canonical_before_tool_callbacks", None) or []
    return apply_reviewed_args in callbacks


def _function(llm_agent: Any, name: str) -> Optional[Callable]:
    """The Python function behind the agent's tool ``name``, where it is a
    function or a FunctionTool."""
    for tool in getattr(llm_agent, "tools", None) or []:
        func = getattr(tool, "func", None)
        if func is not None and getattr(tool, "name", None) == name:
            return func
        if inspect.isfunction(tool) and tool.__name__ == name:
            return tool
    return None


def _misfit(llm_agent: Any, name: str, args: dict) -> Optional[str]:
    """Why the agent's tool ``name`` would not run with ``args`` as they are,
    or None: an argument it does not take, which ADK drops, or one it needs
    and is not given, which ADK refuses."""
    func = _function(llm_agent, name)
    if func is None:
        return f"the agent has no function tool {name} whose arguments the bridge can check"
    parameters = inspect.signature(func).parameters
    taken = {n: p for n, p in parameters.items()
             if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD) and n not in ("tool_context", "input_stream")
             and getattr(p.annotation, "__name__", p.annotation) not in ("ToolContext", "Context")}
    if not any(p.kind is p.VAR_KEYWORD for p in parameters.values()):
        unknown = sorted(set(args) - set(taken))
        if unknown:
            return f"{name} takes no {', '.join(unknown)}"
    missing = [n for n, p in taken.items() if p.default is p.empty and n not in args]
    if missing:
        return f"{name} needs {', '.join(missing)}"
    return None


def _why_not(decision: Decision) -> str:
    """What a confirmation that runs nothing says, for a review that approved nothing."""
    if decision.comment:
        return decision.comment
    if decision.outcome in ("reject", "revise"):
        return "The reviewer rejected this call."
    return f"The review ended without an approval ({decision.outcome}), so the call was not made."


def _edit_runs(llm_agent: Any, artefact: dict, args: dict) -> Optional[str]:
    """Why an edit of the call's arguments to ``args`` would not run as
    decided, or None where it would."""
    if "payload" in artefact:
        return ("this tool asks for a payload of its own, and runs with the arguments the model "
                "wrote, so an edit of them is not run")
    if not _runs_edits(llm_agent):
        return ("ADK runs a call with the arguments the model wrote unless the agent has "
                "before_tool_callback=apply_reviewed_args, and the bridge is given that agent as llm_agent")
    return _misfit(llm_agent, artefact["tool"], args)


@dataclass
class ChapConfirmationBridge:
    """The agent's side of the reviews in one workspace. Share it across a
    run.

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
            client=self.client, on_envelope=self._seen, name="chap-google-adk")

    def _seen(self, method: str, params: dict) -> None:
        if self.on_envelope is not None:
            self.on_envelope(method, params)

    # ---- request and wait -----------------------------------------

    async def request_confirmation(self, paused: Any, *, llm_agent: Any = None,
                                   timeout: Optional[float] = None, poll_seconds: float = 2.0) -> Any:
        """Open a review of a paused call, wait for the person's decision, and
        return the ToolConfirmation to resume the run with.

        ``paused`` is the ``adk_request_confirmation`` call ADK paused the
        run with, or the call it pauses, its ``originalFunctionCall``. Given
        the first, a tool's own question and payload are reviewed with the
        call. ``llm_agent`` is the LlmAgent whose tool the call names: an
        edit of the arguments runs only where it has
        ``before_tool_callback=apply_reviewed_args`` and the tool takes them.
        The review is on a task that requires review, so only a person's
        decision completes it.

        An approval confirms the call. An override of the arguments
        confirms it with ``payload={"args": ...}``, and one of a tool's own
        payload with the person's version of it. Anything else is not
        confirmed, with the person's note as the hint: a rejection, an
        abstention, an edit the run would not carry out as decided, or a
        revision, whose task is then declined, as the model's next call is
        reviewed on a task of its own.

        The task is read every ``poll_seconds``, and the event loop is never
        held up. A call nobody decided within ``timeout`` seconds is not
        confirmed, and its review is withdrawn, so no decision lands on it
        later; None waits as long as the reviewer takes. Cancelling the
        awaiting task withdraws the review too.
        """
        from google.adk.tools.tool_confirmation import ToolConfirmation

        artefact = _artefact(*_paused(paused))
        try:
            decision = await self._review.review_async(
                artefact, kind=artefact["tool"] or "tool_call", input=artefact,
                timeout=timeout, poll_seconds=poll_seconds)
        except TimeoutError:
            return ToolConfirmation(
                confirmed=False, hint=f"Nobody decided on this call within {timeout} seconds, so it was not made.")
        if decision.revision_requested:
            await asyncio.to_thread(self._review.close, decision.task_id, _SENT_BACK)
        if not decision.approved:
            return ToolConfirmation(confirmed=False, hint=_why_not(decision))
        output = decision.output
        unchanged = [key for key in artefact if key not in ("args", "payload")]
        if (not isinstance(output, dict) or output.keys() != artefact.keys()
                or any(output[key] != artefact[key] for key in unchanged)
                or not isinstance(output["args"], dict)):
            return ToolConfirmation(confirmed=False, hint=_NOT_THE_CALL)
        if output["args"] != artefact["args"]:
            why = _edit_runs(llm_agent, artefact, output["args"])
            if why is not None:
                return ToolConfirmation(confirmed=False, hint=f"The reviewer's edit was not made: {why}.")
            return ToolConfirmation(confirmed=True, payload={"args": output["args"]})
        return ToolConfirmation(confirmed=True, payload=output.get("payload"))

    async def wait_for_decision(self, task_id: str, *, timeout: Optional[float] = None,
                                poll_seconds: float = 2.0) -> Decision:
        """Wait for the decision on a review this bridge opened, and return
        it. After ``timeout`` seconds the review is withdrawn and
        TimeoutError raised; None waits as long as the reviewer takes."""
        return await self._review.wait_async(task_id, timeout=timeout, poll_seconds=poll_seconds)

    # ---- record a decision made elsewhere --------------------------

    def record_decision(self, paused: Any, confirmation: Any, *,
                        llm_agent: Any = None,
                        decision: Optional[str] = None,
                        returned: Optional[dict] = None,
                        diff: Optional[list[dict]] = None,
                        approver: Optional[str] = None,
                        rationale: Optional[str] = None,
                        tags: Optional[list[str]] = None,
                        intent_preserved: Optional[bool] = None) -> str:
        """Record a confirmation your own code made, and return the task id.

        ``paused`` is the ``adk_request_confirmation`` call, or the call it
        pauses; ``confirmation`` is the ToolConfirmation the run resumes
        with, and what it makes ADK do is what is recorded. Not confirmed is
        a rejection. Confirmed with ``payload={"args": ...}`` that differ
        from the call's is an override of the arguments, recorded only where
        ADK runs it: ``llm_agent`` has
        ``before_tool_callback=apply_reviewed_args`` and its tool takes
        them. Confirmed with any other payload is the tool's own data, and
        an override of the payload it asked for, recorded as the tool will
        read it. Confirmed otherwise is an approval.

        ``decision``, ``returned`` (the arguments the call runs with) and
        ``diff`` may restate that, and a value that disagrees raises
        ValueError, with nothing recorded. The decision is recorded in the
        approver's name, a person's ``human:`` URI (the bridge's reviewer by
        default), bound to the digest of the call, and marked ``via:
        "bridge-record"``. Where people sign, PersonRequired is raised and
        nothing is recorded: they decide at the desk, through
        request_confirmation.
        """
        if decision not in (None, "approve", "override", "reject"):
            raise ValueError(f"decision must be 'approve', 'override', or 'reject': {decision!r}")
        artefact = _artefact(*_paused(paused))
        decided = dict(artefact)
        payload = _attr(confirmation, "payload")
        if not _attr(confirmation, "confirmed", False):
            carried = "reject"
        elif "payload" in artefact or (payload is not None and not isinstance(_attr(payload, "args"), dict)):
            decided["payload"] = payload
        else:
            edited = payload.get("args") if isinstance(payload, dict) else None
            if edited is not None and edited != artefact["args"]:
                why = _edit_runs(llm_agent, artefact, edited)
                if why is not None:
                    raise ValueError(f"an override is recorded only where ADK runs it, and {why}")
                decided["args"] = dict(edited)
        if _attr(confirmation, "confirmed", False):
            carried = "approve" if decided == artefact else "override"
        if decision is not None and decision != carried and (decision, carried) != ("override", "approve"):
            raise ValueError(f"the confirmation makes this call {carried!r}, so it cannot be recorded as {decision!r}")
        if returned is not None and returned != decided["args"]:
            raise ValueError("`returned` differs from the arguments the confirmation runs the call with")
        if diff is not None and carried == "override":
            try:
                patched = apply_json_patch(artefact, diff)
            except PatchError as exc:
                raise ValueError(f"`diff` does not apply to the call: {exc}") from exc
            if patched != decided:
                raise ValueError("`diff` does not give what the confirmation runs the call with")
        record = dict(task_kind=artefact["tool"] or "tool_call", input=artefact, reviewer=approver, tags=tags)
        if carried == "override":
            return self._review.record(
                artefact, "override", comment=rationale or "reviewer edited the tool call",
                diff=diff if diff is not None else json_patch(artefact, decided),
                intent_preserved=True if intent_preserved is None else intent_preserved, **record).task_id
        return self._review.record(artefact, carried, comment=rationale, **record).task_id

    def audit(self) -> list[dict]:
        """The workspace's audit log."""
        return self._review.audit()


__all__ = [
    "ChapBridgeError",
    "ChapConfirmationBridge",
    "ChapError",
    "Decision",
    "PersonRequired",
    "apply_reviewed_args",
]
