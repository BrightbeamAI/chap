"""
chap-llama-index: put a LlamaIndex Workflow's proposed output in front of a
person, as a CHAP review, and answer the workflow with what they decide.

A Workflow step pauses for human input with an InputRequiredEvent, and a
HumanResponseEvent answers it. request_response opens a review of the
output the input event proposes, waits for the person's decision, made at a
CHAP desk, and returns the HumanResponseEvent that answers the pause: its
``response`` is the output as decided, or the person's note when nothing
was approved, and its ``decision`` says how the review ended.

record_decision records a reply your own code made, in the reviewer's name,
marked as recorded, in a workspace where nobody signs:

    decision      CHAP envelope
    --------      ---------------------------
    approve       decide.approve
    override      decide.override  (proposed vs returned, as a diff)
    reject        decide.reject

The bridge depends on chap-coordinator alone: it imports
llama-index-workflows only to build the event request_response returns, and
reads events through ``event.get(...)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from chap_coordinator.bridge import ChapError, Decision, PersonRequired, ReviewClient, json_patch

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-llama-index")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"

#: The name chap-llama-index 0.3.4 gave the coordinator's refusals.
ChapBridgeError = ChapError

#: The fields request_response sets on the HumanResponseEvent it returns.
_ANSWER = ("response", "decision", "user_name", "rationale", "tags", "chap_task_id")


def _event_get(event: Any, key: str, default: Any = None) -> Any:
    """A field of a Workflows event, a DictLikeModel whose fields come
    through .get(); a stand-in without .get() is read by attribute."""
    getter = getattr(event, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(event, key, default)


def _payload(event: Any) -> dict:
    """A copy of an event's fields."""
    to_dict = getattr(event, "to_dict", None)
    return dict(to_dict()) if callable(to_dict) else {}


def _proposed(event: Any) -> Any:
    """What an InputRequiredEvent puts up for review: its ``proposed`` field,
    or else all of its fields."""
    return _event_get(event, "proposed") or _payload(event)


def _note(decision: Decision) -> str:
    """The response to a review that approved nothing."""
    if decision.comment:
        return decision.comment
    if decision.outcome in ("reject", "revise"):
        return "The reviewer rejected the output."
    return f"The review ended without an approval ({decision.outcome})."


@dataclass
class ChapHitlBridge:
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
            client=self.client, on_envelope=self._seen, name="chap-llama-index")

    def _seen(self, method: str, params: dict) -> None:
        if self.on_envelope is not None:
            self.on_envelope(method, params)

    # ---- request and wait -----------------------------------------

    async def request_response(self, input_event: Any, *, proposed: Any = None,
                               task_id: Optional[str] = None, timeout: Optional[float] = None,
                               poll_seconds: float = 2.0) -> Any:
        """Open a review of the output an InputRequiredEvent proposes, wait
        for the person's decision, and return the HumanResponseEvent that
        answers the pause.

        The review is on a task that requires review, so only a person's
        decision completes it. The event carries:

            response      the output as decided, the person's version after
                          an edit; or their note when nothing was approved
            decision      approve, override, revise (sent back for another),
                          reject, abstain, or another ending of the review
            user_name     the person who decided
            rationale     their note
            tags          their tags
            chap_task_id  the CHAP task

        and every other field of the input event, so a step that waits with
        ``ctx.wait_for_event(HumanResponseEvent, requirements=...)`` on a
        field such as ``request_id`` receives it.

        ``proposed`` defaults to the input event's ``proposed`` field, or
        else all of its fields. With ``task_id``, the ``chap_task_id`` of a
        response whose decision was ``revise``, the output is the next one on
        that task, and opens the review's next round. Where people sign, the
        review opens once a person has joined from the desk.

        The task is read every ``poll_seconds``, and the event loop is never
        held up. After ``timeout`` seconds the review is withdrawn, so no
        decision lands on it later, and TimeoutError raised; None waits as
        long as the reviewer takes. Cancelling the awaiting task withdraws
        the review too.
        """
        from workflows.events import HumanResponseEvent

        if proposed is None:
            proposed = _proposed(input_event)
        decision = await self._review.review_async(
            proposed, kind="workflow_output", input=proposed, task_id=task_id,
            timeout=timeout, poll_seconds=poll_seconds)
        answer = {key: value for key, value in _payload(input_event).items() if key not in _ANSWER}
        return HumanResponseEvent(
            **answer,
            response=decision.output if decision.approved else _note(decision),
            decision=decision.outcome,
            user_name=decision.reviewer,
            rationale=decision.comment,
            tags=list(decision.tags),
            chap_task_id=decision.task_id,
        )

    async def wait_for_decision(self, task_id: str, *, timeout: Optional[float] = None,
                                poll_seconds: float = 2.0) -> Decision:
        """Wait for the decision on a review this bridge opened, and return
        it. After ``timeout`` seconds the review is withdrawn and
        TimeoutError raised; None waits as long as the reviewer takes."""
        return await self._review.wait_async(task_id, timeout=timeout, poll_seconds=poll_seconds)

    # ---- record a decision made elsewhere --------------------------

    def record_decision(self, input_event: Any, response_event: Any, *,
                        decision: str,
                        proposed: Any = None,
                        returned: Any = None,
                        diff: Optional[list[dict]] = None,
                        rationale: Optional[str] = None,
                        tags: Optional[list[str]] = None,
                        intent_preserved: Optional[bool] = None,
                        approver: Optional[str] = None) -> str:
        """Record a decision your own code made, and return the task id.

        ``input_event`` is the InputRequiredEvent that paused the step, and
        ``response_event`` the HumanResponseEvent that answered it. Workflows
        events carry no approve, edit or reject signal, so ``decision``
        says which it was. ``proposed``, ``returned``, ``rationale``,
        ``tags``, ``intent_preserved`` and ``approver`` default to fields
        of the events: the input's ``proposed``, and the response's
        ``response``, ``rationale``, ``tags``, ``intent_preserved`` and
        ``user_name``. An override is recorded as the RFC 6902 patch from
        the proposed output to the returned one, unless ``diff`` gives it;
        one that changes nothing is the approval it is.

        The decision is recorded in the approver's name, a person's
        ``human:`` URI (the bridge's reviewer by default), bound to the
        digest of the proposed output and marked ``via: "bridge-record"``.
        Where people sign, PersonRequired is raised and nothing is recorded:
        they decide at the desk, through request_response.
        """
        if decision not in ("approve", "override", "reject"):
            raise ValueError(f"decision must be 'approve', 'override', or 'reject': {decision!r}")
        approver = approver or _event_get(response_event, "user_name") or self.reviewer
        if proposed is None:
            proposed = _proposed(input_event)
        if rationale is None:
            rationale = _event_get(response_event, "rationale")
        if tags is None:
            tags = _event_get(response_event, "tags")
        record = dict(task_kind="workflow_output", input=proposed, reviewer=approver, tags=tags)
        if decision == "override":
            if diff is None:
                if returned is None:
                    returned = _event_get(response_event, "response")
                if returned is None:
                    raise ValueError("an override needs `returned` content (or a `diff`)")
                diff = json_patch(proposed, returned)
            if diff:
                if intent_preserved is None:
                    intent_preserved = _event_get(response_event, "intent_preserved")
                return self._review.record(
                    proposed, "override", comment=rationale or "reviewer edited the output", diff=diff,
                    intent_preserved=True if intent_preserved is None else intent_preserved, **record).task_id
            decision = "approve"
        if decision == "approve":
            return self._review.record(proposed, "approve", comment=rationale, **record).task_id
        note = rationale or _event_get(response_event, "response")
        return self._review.record(proposed, "reject", comment=note if isinstance(note, str) else None,
                                   **record).task_id

    def audit(self) -> list[dict]:
        """The workspace's audit log."""
        return self._review.audit()


__all__ = [
    "ChapBridgeError",
    "ChapError",
    "ChapHitlBridge",
    "Decision",
    "PersonRequired",
]
