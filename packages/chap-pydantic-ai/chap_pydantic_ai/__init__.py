"""
chap-pydantic-ai: put a Pydantic AI tool call that needs approval in front
of a person, as a CHAP review, and carry out what they decide.

Pydantic AI gates a tool with requires_approval=True (or an
ApprovalRequiredToolset). The run then ends with a DeferredToolRequests
output whose .approvals holds the pending ToolCallParts, and the caller
resumes it with DeferredToolResults. request_results opens a review of each
pending call, waits for the person's decisions, made at a CHAP desk, and
returns the DeferredToolResults that carry them out:

    CHAP decision                        Pydantic AI resolution
    ---------------------------------    ---------------------------------
    decide.approve                       ToolApproved()
    decide.override of the arguments     ToolApproved(override_args=...)
    anything else, or no decision        ToolDenied(message=...)

record_results records a resolution your own code made, in the reviewer's
name, marked as recorded, in a workspace where nobody signs:

    Pydantic AI resolution               CHAP envelope
    ---------------------------------    ---------------------------------
    True / ToolApproved()                decide.approve
    ToolApproved(override_args=...)      decide.override (a diff of the args)
    False / ToolDenied(message=...)      decide.reject

The proposed call, its tool name, arguments and tool_call_id, is the
artefact under review. The bridge depends on chap-coordinator alone: it
imports Pydantic AI only to build what request_results returns, and reads
the resolution objects by their attributes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from chap_coordinator.bridge import (
    ChapError, Decision, PersonRequired, ReviewClient, decided_arguments, json_patch,
)

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-pydantic-ai")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"

#: The name chap-pydantic-ai 0.3.4 gave the coordinator's refusals.
ChapBridgeError = ChapError

_SENT_BACK = ("The reviewer asked for another call, so the model's next call is reviewed on a task "
              "of its own.")


# ---- reading Pydantic AI objects without importing them --------------


def _arguments(call: Any) -> dict:
    """A pending call's arguments as a dict. ToolCallPart.args is a str, a
    dict or None; args_as_dict() reads either, so a model that sent JSON
    text and one that sent a dict make the same artefact."""
    as_dict = getattr(call, "args_as_dict", None)
    if callable(as_dict):
        return as_dict()
    args = getattr(call, "args", None)
    if isinstance(args, str):
        return json.loads(args) if args.strip() else {}
    return dict(args) if args else {}


def _artefact(call: Any) -> dict:
    """The proposed tool call, as the artefact under review."""
    return {"tool": getattr(call, "tool_name", None), "args": _arguments(call),
            "tool_call_id": getattr(call, "tool_call_id", None)}


def _is_approved(resolution: Any) -> bool:
    if isinstance(resolution, bool):
        return resolution
    return hasattr(resolution, "override_args")  # a ToolApproved


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


def _left(deadline: Optional[float]) -> Optional[float]:
    return None if deadline is None else max(0.0, deadline - time.monotonic())


@dataclass
class ChapApprovalBridge:
    """The agent's side of the reviews in one workspace. Share it across
    runs.

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
            client=self.client, on_envelope=self._seen, name="chap-pydantic-ai")

    def _seen(self, method: str, params: dict) -> None:
        if self.on_envelope is not None:
            self.on_envelope(method, params)

    # ---- request and wait -----------------------------------------

    def request_results(self, requests: Any, *, timeout: Optional[float] = None,
                        poll_seconds: float = 2.0) -> Any:
        """Open a review of every pending approval in a DeferredToolRequests,
        wait for the person's decisions, and return the DeferredToolResults
        that carry them out.

        Each call is reviewed on its own task, which requires review, so
        only a person's decision completes it. An approval runs the call as
        proposed, and an override of its arguments runs it with the
        person's. Anything else denies it, with the person's note as the
        message the model reads: a rejection, a revision request, an
        abstention, or an edit to anything but the arguments, which the run
        cannot carry out. A call sent back for another is declined, and the
        model's next call is reviewed on a task of its own.

        The reviews all open before the first wait, so the person sees them
        together; where people sign, they open once a person has joined from
        the desk. The tasks are read every ``poll_seconds``. A call nobody
        decided within ``timeout`` seconds is denied and its review
        withdrawn, so no decision lands on it later; None waits as long as
        the reviewer takes. Interrupted, the bridge withdraws every review
        still open before the interrupt propagates.

        Each result's metadata names the CHAP task, the reviewer and the
        ``outcome``: approve, override, revise, reject, abstain, another
        ending, or ``no decision``. The tool reads it as
        ``ctx.tool_call_metadata``.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        opened: list[tuple[dict, Optional[str]]] = []
        decided: dict[str, Decision] = {}
        try:
            for call in requests.approvals:
                artefact = _artefact(call)
                try:
                    task_id = self._review.open(artefact, kind=artefact["tool"] or "tool_call", input=artefact,
                                                timeout=_left(deadline), poll_seconds=poll_seconds)
                except TimeoutError:
                    task_id = None
                opened.append((artefact, task_id))
            for _, task_id in opened:
                if task_id is None:
                    continue
                try:
                    decided[task_id] = self._review.wait(task_id, timeout=_left(deadline),
                                                         poll_seconds=poll_seconds)
                except TimeoutError:
                    continue
                if decided[task_id].revision_requested:
                    self._review.close(task_id, _SENT_BACK)
        except BaseException:
            self._withdraw(task_id for _, task_id in opened if task_id is not None and task_id not in decided)
            raise
        return self._results(opened, decided, timeout)

    async def request_results_async(self, requests: Any, *, timeout: Optional[float] = None,
                                    poll_seconds: float = 2.0) -> Any:
        """:meth:`request_results` for async code, such as an agent run with
        ``await agent.run(...)``. The event loop is never held up, and
        cancelling the awaiting task withdraws every review still open."""
        deadline = None if timeout is None else time.monotonic() + timeout
        opened: list[tuple[dict, Optional[str]]] = []
        decided: dict[str, Decision] = {}
        try:
            for call in requests.approvals:
                artefact = _artefact(call)
                try:
                    task_id = await self._review.open_async(
                        artefact, kind=artefact["tool"] or "tool_call", input=artefact,
                        timeout=_left(deadline), poll_seconds=poll_seconds)
                except TimeoutError:
                    task_id = None
                opened.append((artefact, task_id))
            for _, task_id in opened:
                if task_id is None:
                    continue
                try:
                    decided[task_id] = await self._review.wait_async(task_id, timeout=_left(deadline),
                                                                     poll_seconds=poll_seconds)
                except TimeoutError:
                    continue
                if decided[task_id].revision_requested:
                    await asyncio.to_thread(self._review.close, task_id, _SENT_BACK)
        except BaseException:
            undecided = [task_id for _, task_id in opened if task_id is not None and task_id not in decided]
            await asyncio.to_thread(self._withdraw, undecided)
            raise
        return self._results(opened, decided, timeout)

    def _withdraw(self, task_ids: Iterable[str]) -> None:
        """Withdraw reviews nobody waits for now, on the way out of an
        interrupt or an error, which propagates whatever happens here."""
        for task_id in task_ids:
            with contextlib.suppress(Exception):
                self._review.withdraw(task_id, "The agent stopped waiting for a decision.")

    def _results(self, opened: list[tuple[dict, Optional[str]]], decided: dict[str, Decision],
                 timeout: Optional[float]) -> Any:
        from pydantic_ai import DeferredToolResults, ToolApproved, ToolDenied

        results = DeferredToolResults()
        for artefact, task_id in opened:
            call_id = artefact["tool_call_id"]
            decision = decided.get(task_id) if task_id is not None else None
            if decision is None:
                results.approvals[call_id] = ToolDenied(
                    f"Nobody decided on this call within {timeout} seconds, so it was not made.")
                results.metadata[call_id] = {"chap_task_id": task_id, "reviewer": None, "outcome": "no decision"}
                continue
            args = decided_arguments(decision, artefact)
            if args is None:
                results.approvals[call_id] = ToolDenied(_why_not(decision))
            else:
                results.approvals[call_id] = (ToolApproved() if args == artefact["args"]
                                              else ToolApproved(override_args=args))
            results.metadata[call_id] = {"chap_task_id": task_id, "reviewer": decision.reviewer,
                                         "outcome": decision.outcome}
        return results

    def wait_for_decision(self, task_id: str, *, timeout: Optional[float] = None,
                          poll_seconds: float = 2.0) -> Decision:
        """Wait for the decision on a review this bridge opened, such as the
        one ``results.metadata[tool_call_id]["chap_task_id"]`` names, and
        return it. After ``timeout`` seconds the review is withdrawn and
        TimeoutError raised; None waits as long as the reviewer takes."""
        return self._review.wait(task_id, timeout=timeout, poll_seconds=poll_seconds)

    # ---- record a decision made elsewhere --------------------------

    def record_decision(self, call: Any, resolution: Any, *,
                        approver: Optional[str] = None,
                        rationale: Optional[str] = None,
                        tags: Optional[list[str]] = None,
                        intent_preserved: Optional[bool] = None) -> str:
        """Record one resolved approval, made in your own code, and return
        the task id.

        ``call`` is the pending ToolCallPart; ``resolution`` is what your
        code put in DeferredToolResults.approvals: a bool, a ToolApproved or
        a ToolDenied. The keyword arguments carry what the resolution cannot:
        the person who decided, a ``human:`` URI (the bridge's reviewer by
        default), their rationale, tags, and whether an edit kept the call's
        intent, which it does by default.

        The decision is recorded in the approver's name, bound to the digest
        of the call, and marked ``via: "bridge-record"``. Where people sign,
        PersonRequired is raised and nothing is recorded: they decide at the
        desk, through request_results.
        """
        artefact = _artefact(call)
        override = getattr(resolution, "override_args", None)
        diff = json_patch(artefact["args"], override, "/args") if override is not None else []
        if diff:
            decision = self._review.record(
                artefact, "override", task_kind=artefact["tool"] or "tool_call", input=artefact,
                reviewer=approver, comment=rationale or "reviewer edited tool arguments", tags=tags,
                diff=diff, intent_preserved=True if intent_preserved is None else intent_preserved)
        elif _is_approved(resolution):
            decision = self._review.record(artefact, "approve", task_kind=artefact["tool"] or "tool_call",
                                           input=artefact, reviewer=approver, comment=rationale, tags=tags)
        else:
            note = rationale or getattr(resolution, "message", None) or "The tool call was denied."
            decision = self._review.record(artefact, "reject", task_kind=artefact["tool"] or "tool_call",
                                           input=artefact, reviewer=approver, comment=note, tags=tags)
        return decision.task_id

    def record_results(self, requests: Any, results: Any) -> list[str]:
        """Record every approval in a DeferredToolResults against the
        DeferredToolRequests it answers, and return the task ids in request
        order. The approver, rationale, tags and intent_preserved are read
        per call from ``results.metadata[tool_call_id]``."""
        meta = getattr(results, "metadata", None) or {}
        task_ids = []
        for call in requests.approvals:
            call_id = getattr(call, "tool_call_id", None)
            if call_id not in results.approvals:
                raise ValueError(f"no resolution for pending approval {call_id!r}")
            signal = meta.get(call_id, {})
            task_ids.append(self.record_decision(
                call, results.approvals[call_id], approver=signal.get("approver"),
                rationale=signal.get("rationale"), tags=signal.get("tags"),
                intent_preserved=signal.get("intent_preserved")))
        return task_ids

    def audit(self) -> list[dict]:
        """The workspace's audit log."""
        return self._review.audit()


__all__ = [
    "ChapApprovalBridge",
    "ChapBridgeError",
    "ChapError",
    "Decision",
    "PersonRequired",
]
