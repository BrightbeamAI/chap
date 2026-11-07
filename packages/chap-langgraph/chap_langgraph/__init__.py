"""
chap-langgraph: put a LangGraph draft in front of a person, as a CHAP
review, and carry on with what they decide.

A node that makes a draft opens its review with hil_review. The next node
waits for the person's decision, made at a CHAP desk, and applies it:

    def draft(state):
        return hil_review(bridge, produce_draft(state), kind="draft_response")

    def review(state):
        decision = bridge.wait_for_decision(state["chap_task_id"])
        return {"reply": bridge.apply_decision(state["chap_task_id"], decision,
                                               state["chap_artefact"])}

LangGraph runs a node again from its first line when the graph resumes, so
the review is opened in one node and decided in the next: the draft and
its review are made once, and the decision lands on the draft the person
was shown. apply_decision returns what the coordinator holds as decided,
the draft as shown or the person's version of it, and raises
ReviewRejected for anything else.

A decision made in your own interface, sent back as the graph's resume
value, is recorded in the reviewer's name, marked as recorded, in a
workspace where nobody signs; where people sign, they decide at the desk.

The bridge depends on chap-coordinator alone and imports no LangGraph: it
takes dict-shaped state and returns LangGraph-shaped values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from chap_coordinator.bridge import (
    ChapError, Decision, PersonRequired, ReviewClient, ReviewRejected, artefact_digest,
    wait_for_decision,
)

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-langgraph")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"

#: The name chap-langgraph 0.3.4 gave the coordinator's refusals.
ChapBridgeError = ChapError


@dataclass
class ChapBridge:
    """The agent's side of the reviews in one workspace. One bridge per
    workspace; share it across nodes.

    ``coord`` is a Coordinator in this process, or a desk's, through
    ``chap_coordinator.client``: an HttpCoordinator, or a Participant for the
    agent, which signs the agent's calls with its key. The bridge joins the
    agent when it is made. A review is addressed to every person in the
    workspace; ``reviewer`` names the person a decision recorded from your
    own interface is made in, who joins when the first review opens where
    nobody signs. Where people sign, each joins from the desk with their own
    key, and the bridge joins nobody in their name.

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
            client=self.client, on_envelope=self._seen, name="chap-langgraph")

    def _seen(self, method: str, params: dict) -> None:
        if self.on_envelope is not None:
            self.on_envelope(method, params)

    def wait_for_decision(self, task_id: str, *, timeout: Optional[float] = None,
                          poll_seconds: float = 2.0) -> Decision:
        """Wait for the decision on a review hil_review opened, and return it.

        The task is read every ``poll_seconds`` until a decision takes it out
        of review. Raises TimeoutError after ``timeout`` seconds; None waits
        as long as the reviewer takes. A wait that stops leaves the review
        open: the graph's state holds its task id, and resuming the graph
        waits on it again. :meth:`withdraw` gives a draft up.
        """
        return wait_for_decision(self._review.read, task_id, timeout=timeout, poll_seconds=poll_seconds)

    def withdraw(self, task_id: str, note: str = "The graph gave this draft up.") -> Optional[Decision]:
        """Take a review back, so no decision lands on a draft the graph will
        not carry on with, and return None; or the decision, where one
        landed first. ``hil_review`` with ``existing_task_id`` opens the
        task's next round."""
        return self._review.withdraw(task_id, note)

    def apply_decision(self, task_id: str, decision: Union[str, dict[str, Any], Decision],
                       artefact: Any) -> Any:
        """Apply the decision on the draft under review, and return the draft
        to carry on with, as the coordinator holds it: the draft the person
        was shown when approved, their version when overridden. Anything
        else raises ReviewRejected, whose ``outcome`` says how the review
        ended: reject, revise when the person asked for another draft,
        abstain, or another ending.

        ``decision`` is a Decision from wait_for_decision, made at a desk and
        recorded there, which is applied and not recorded again; or the
        resume value of a decision made in your own interface:

            "approve"
            "reject"
            {"action": "approve" | "reject" | "override", ...}
            {"diff": [...], "rationale": "..."}  (an override)

        A payload takes ``rationale``, ``tags``, a rejection's ``category``
        and ``request_revision``, and an override's ``diff`` and
        ``intent_preserved``. It is recorded in the reviewer's name, bound to
        ``approved_artefact_digest``: the one the payload names, where your
        interface sends back the digest of what it showed, or else the
        digest of ``artefact``, the draft as the graph's state holds it. The
        coordinator refuses it unless that is the draft under review. A
        decision recorded once is read back, and not recorded again, when
        the node runs again. Where people sign, PersonRequired is raised and
        nothing is recorded: they decide at the desk.
        """
        if isinstance(decision, Decision):
            if decision.task_id != task_id:
                raise ValueError(f"the decision is on {decision.task_id}; apply_decision was given {task_id}")
        else:
            kind, payload = _read_payload(decision)
            decision = self._review.decide(
                task_id, kind,
                digest=payload.get("approved_artefact_digest") or artefact_digest(artefact),
                comment=payload.get("rationale"), tags=payload.get("tags"),
                category=payload.get("category"),
                request_revision=bool(payload.get("request_revision")),
                diff=payload.get("diff"), intent_preserved=payload.get("intent_preserved"))
        if decision.approved:
            return decision.output
        raise ReviewRejected.from_decision(decision)

    def audit(self) -> list[dict[str, Any]]:
        """The workspace's audit log."""
        return self._review.audit()


def _read_payload(decision: Union[str, dict[str, Any]]) -> tuple[str, dict]:
    """The kind of decision a resume value records, and its other fields."""
    if isinstance(decision, str):
        kind, payload = decision, {}
    elif isinstance(decision, dict):
        payload = dict(decision)
        kind = payload.pop("action", None)
        if kind is None and "diff" in payload:
            kind = "override"
        if kind is None:
            raise ValueError("a decision payload needs an explicit 'action' (approve, reject or override)")
    else:
        raise TypeError(f"unexpected decision payload: {type(decision)}")
    if kind not in ("approve", "reject", "override"):
        raise ValueError(f"unknown decision action: {kind!r}")
    if kind == "override" and not payload.get("diff"):
        raise ValueError("an override needs a 'diff', an RFC 6902 patch of the draft")
    return kind, payload


def hil_review(bridge: ChapBridge, artefact: Any, kind: str, input_: Any = None,
               existing_task_id: Optional[str] = None, *, prefix: str = "chap_") -> dict[str, Any]:
    """
    Open a review of ``artefact``, and return the state keys that carry it
    to the node that waits for the decision.

    Call it in the node that makes the draft, and return its result from
    that node: the review is then opened once per draft, and its task id
    checkpointed before the next node waits or interrupts. Opened in the
    node that interrupts, it would run again on resume: the draft would be
    made a second time and a second review opened, and the decision would
    land on a draft the person never saw.

    The task requires review, so only a person's decision completes it.
    ``input_`` is the task's input, such as the ticket the draft answers.
    With ``existing_task_id`` the draft is the next one on a task whose
    reviewer asked for another, or whose review was withdrawn, and opens the
    review's next round. Where people sign, the review opens once a person
    has joined from the desk; until then hil_review waits. The keys are:

        chap_task_id          The CHAP task id, for wait_for_decision and
                              apply_decision.
        chap_artefact         The draft under review.
        chap_artefact_digest  Its digest, the approved_artefact_digest a
                              decision on it carries.
        chap_kind             The task kind.

    ``prefix`` begins each key, ``chap_`` by default, so drafts made in
    parallel branches each write keys of their own.
    """
    task_id = bridge._review.open(artefact, kind=kind, input=input_ or {}, task_id=existing_task_id)
    return {
        f"{prefix}task_id":         task_id,
        f"{prefix}artefact":        artefact,
        f"{prefix}artefact_digest": artefact_digest(artefact),
        f"{prefix}kind":            kind,
    }


__all__ = [
    "ChapBridge",
    "ChapBridgeError",
    "ChapError",
    "Decision",
    "PersonRequired",
    "ReviewRejected",
    "hil_review",
]
