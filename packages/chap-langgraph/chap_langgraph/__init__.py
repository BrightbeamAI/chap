"""
chap-langgraph: glue between LangGraph workflows and a CHAP Coordinator.

The point of this adapter is to make the human-in-the-loop moment in a
LangGraph workflow audit-visible. LangGraph pauses a graph with interrupt()
and resumes it with Command(resume=...). This adapter turns those events
into CHAP envelopes:

    LangGraph event              CHAP envelope
    -----------------            -------------------------
    a draft is ready             task.create + review.request
    Command(resume=...) sent     decide.approve | decide.reject | decide.override

LangGraph runs a node again from its first line when the graph resumes. So
the review is opened in one node and the decision applied in the next: the
node that drafts and opens the review finishes before the graph pauses, its
result is checkpointed with the review's task id, and resuming runs only the
node that paused.

Usage:

    from chap_coordinator import Coordinator
    from chap_langgraph import ChapBridge, ReviewRejected, hil_review
    from langgraph.types import interrupt

    coord = Coordinator(enable_chain=True)
    bridge = ChapBridge(
        coord,
        workspace="wsp_my_app",
        agent="agent:drafter#v1",
        reviewer="human:alice@example.org",
    )

    def draft(state):
        return hil_review(bridge, produce_draft(state), kind="draft_response")

    def review(state):
        decision = interrupt({"task_id": state["chap_task_id"],
                              "draft": state["chap_artefact"]})
        try:
            reply = bridge.apply_decision(state["chap_task_id"], decision,
                                          state["chap_artefact"])
        except ReviewRejected:
            reply = None
        return {"reply": reply}

apply_decision records the decision under the reviewer's URI and returns
the artefact to carry on: the draft as shown when approved, the reviewer's
version when overridden. A rejection raises ReviewRejected, so a rejected
draft goes no further.

When the person decides at a CHAP desk, the decision is on the chain
already: wait_for_decision reads it back, and apply_decision applies it
without recording it a second time.

The adapter depends on neither langgraph nor langchain at import time. Pass
LangGraph state dicts in and get LangGraph-shaped values out.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from chap_coordinator import Coordinator, content_hash


try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-langgraph")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"


def _make_envelope(method: str, params: dict) -> dict:
    """Build a JSON-RPC 2.0 envelope. The id is opaque to dispatch."""
    return {
        "jsonrpc": "2.0",
        "id":      f"chap-langgraph-{method}",
        "method":  method,
        "params":  params,
    }


# ============================================================
#   Decisions read back from the coordinator
# ============================================================


# A task in these states still waits for its reviewer: under review, or
# paused during its review.
_UNDECIDED = ("review_requested", "paused")


@dataclass(frozen=True)
class Decision:
    """A reviewer's decision on a review, as the coordinator recorded it.

    `approved` is true when the task completed through an approval or an
    override, and `output` is then the artefact as decided: the draft as it
    was, or the reviewer's version. Any other ending, a rejection, an
    abstention, or a review escalated or cancelled, approves nothing, and
    `output` is None.
    """

    task_id:  str
    state:    str
    kind:     Optional[str] = None   # approve, override, reject or abstain
    reviewer: Optional[str] = None
    output:   Any = None
    comment:  Optional[str] = None   # the reviewer's note, or an override's rationale
    tags:     tuple = ()

    @property
    def approved(self) -> bool:
        return self.state == "completed" and self.kind in ("approve", "override")

    @property
    def revision_requested(self) -> bool:
        """The reviewer rejected the draft and asked the agent for another."""
        return self.kind == "reject" and self.state == "in_progress"


def _read_task(source: Any, workspace: str, task_id: str) -> Optional[dict]:
    """The task as GET /api/tasks/<id> shows it: its state, its output and
    its review's decisions, or None when the task is unknown.

    An in-process Coordinator is read directly. Any other source is read
    with source.task(task_id), the call a desk client offers."""
    if isinstance(source, Coordinator):
        ws = source.workspaces.get(workspace)
        task = ws.tasks.get(task_id) if ws is not None else None
        if task is None:
            return None
        decisions = []
        for decided in (task.review.decisions if task.review else []):
            decided = dict(decided)
            override = ws.overrides.get(decided.get("override_artefact_id") or "")
            if override is not None:
                decided["rationale"] = override.rationale
            decisions.append(decided)
        return {"state": task.state, "output": task.output,
                "review": {"decisions": decisions}}
    read = getattr(source, "task", None)
    if not callable(read):
        raise TypeError(
            "waiting for a decision reads the task: give the bridge an "
            "in-process Coordinator, or a client with task(task_id) as `client`"
        )
    return read(task_id)


def _decision(task_id: str, view: dict) -> Decision:
    """The decision that ended the review: the last one recorded on it."""
    decisions = (view.get("review") or {}).get("decisions") or []
    last = decisions[-1] if decisions else {}
    decision = Decision(
        task_id=task_id,
        state=view.get("state"),
        kind=last.get("kind"),
        reviewer=last.get("reviewer"),
        comment=last.get("comment") or last.get("rationale"),
        tags=tuple(last.get("tags") or ()),
    )
    if decision.approved:
        decision = dataclasses.replace(decision, output=view.get("output"))
    return decision


# ============================================================
#   ChapBridge
# ============================================================


@dataclass
class ChapBridge:
    """
    Holds the CHAP Coordinator, the workspace it writes into, and the
    default participants for one LangGraph deployment. One bridge per
    workspace; share it across nodes.

    The bridge joins the agent when it is made. It joins the reviewer only
    when it records a decision in the reviewer's name, so a person who
    decides at a desk joins there, with their own key.
    """

    coord:     Coordinator
    workspace: str
    agent:     str
    reviewer:  str
    profiles:  tuple = ("core/1.0", "review/1.0")
    # Hook for tests / consumers who want to see every envelope before
    # it hits the Coordinator. Receives the params dict.
    on_envelope: Optional[Callable[[str, dict], None]] = None
    # Reads a task for wait_for_decision: anything with task(task_id)
    # returning the GET /api/tasks/<id> view. Leave it unset with an
    # in-process Coordinator, which is read directly.
    client: Any = None
    _reviewer_joined: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.reviewer.startswith("human:"):
            raise ValueError(
                "reviewer must be a 'human:' URI for a human-in-the-loop "
                f"bridge, got {self.reviewer!r}"
            )
        if self.reviewer == self.agent:
            raise ValueError(
                "the reviewer cannot be the agent whose work is under review: "
                f"{self.reviewer!r}"
            )
        # Ensure the workspace exists. workspace.create is idempotent
        # on the same id: the second call is a no-op for the data
        # model and produces a clean error rather than corrupting state.
        _safe_dispatch(self.coord, "workspace.create", {
            "workspace": self.workspace,
            "profiles":  list(self.profiles),
        })
        _safe_dispatch(self.coord, "participant.join", {
            "workspace": self.workspace,
            "from":      self.agent,
            "type":      "agent",
        })

    # ---- task lifecycle helpers ----------------------------------

    def create_task(self, kind: str, input_: Any,
                    review_required: bool = False) -> str:
        """Open a task that an agent is about to complete. A task that
        requires review completes only through a reviewer's decision."""
        params: dict = {
            "workspace": self.workspace,
            "from":      self.agent,
            "assignee":  self.agent,
            "kind":      kind,
            "input":     input_,
        }
        if review_required:
            params["review_required"] = True
        env = self._dispatch("task.create", params)
        return env["result"]["task_id"]

    def request_review(self, task_id: str, artefact: Any) -> None:
        """Ask the reviewer to review the artefact. The task waits under
        review until the reviewer decides."""
        self._dispatch("review.request", {
            "workspace": self.workspace,
            "from":      self.agent,
            "task_id":   task_id,
            "artefact":  artefact,
            "to":        self.reviewer,
        })

    def complete_with_review(self, task_id: str, artefact: Any) -> None:
        """
        Record the agent's output and immediately request human review.
        The task reads completed until the review.request reopens it;
        hil_review opens its review with request_review alone.
        """
        self._dispatch("task.complete", {
            "workspace": self.workspace,
            "from":      self.agent,
            "task_id":   task_id,
            "output":    artefact,
        })
        self.request_review(task_id, artefact)

    # ---- decision helpers ----------------------------------------
    #
    # Each records a decision in the reviewer's name. `rationale` is the
    # reviewer's note; decide.approve and decide.reject carry it as
    # `comment`, decide.override as `rationale`. An
    # `approved_artefact_digest` binds the decision to the artefact the
    # reviewer was shown: the coordinator refuses it for any other.

    def approve(self, task_id: str, rationale: Optional[str] = None,
                approved_artefact_digest: Optional[str] = None) -> None:
        params: dict = {
            "workspace": self.workspace,
            "from":      self.reviewer,
            "task_id":   task_id,
        }
        if rationale is not None:
            params["comment"] = rationale
        if approved_artefact_digest is not None:
            params["approved_artefact_digest"] = approved_artefact_digest
        self._join_reviewer()
        self._dispatch("decide.approve", params)

    def reject(self, task_id: str, rationale: str,
               category: Optional[str] = None,
               approved_artefact_digest: Optional[str] = None) -> None:
        params: dict = {
            "workspace": self.workspace,
            "from":      self.reviewer,
            "task_id":   task_id,
            "comment":   rationale,
        }
        if category is not None:
            params["category"] = category
        if approved_artefact_digest is not None:
            params["approved_artefact_digest"] = approved_artefact_digest
        self._join_reviewer()
        self._dispatch("decide.reject", params)

    def override(self, task_id: str, diff: list[dict[str, Any]],
                 rationale: str,
                 tags: Optional[list[str]] = None,
                 intent_preserved: Optional[bool] = None,
                 approved_artefact_digest: Optional[str] = None) -> Any:
        """
        Apply an RFC-6902 JSON Patch to the agent's draft and record
        the resulting artefact. Returns the applied (post-patch) value
        so LangGraph can route it forward.
        """
        params: dict = {
            "workspace": self.workspace,
            "from":      self.reviewer,
            "task_id":   task_id,
            "diff":      diff,
            "rationale": rationale,
            "tags":      tags or [],
        }
        if intent_preserved is not None:
            params["intent_preserved"] = intent_preserved
        if approved_artefact_digest is not None:
            params["approved_artefact_digest"] = approved_artefact_digest
        self._join_reviewer()
        env = self._dispatch("decide.override", params)
        return env["result"]["applied"]

    def apply_decision(self, task_id: str,
                       decision: Union[str, dict[str, Any], Decision],
                       artefact: Any) -> Any:
        """
        Apply the reviewer's decision on `artefact`, the draft the reviewer
        was shown, and return the artefact to carry on: `artefact` itself
        when approved, the reviewer's version when overridden. Anything else
        raises ReviewRejected, so a rejected draft goes no further.

        `decision` is the resume payload of a decision made in your own
        interface, recorded here in the reviewer's name:

            "approve"
            "reject"
            {"action": "approve" | "reject" | "override", ...}
            {"diff": [...], "rationale": "..."}  (implied override)

        or a Decision from wait_for_decision, made at a desk and recorded
        there, which is applied and not recorded again.

        A recorded decision carries `approved_artefact_digest`: the one the
        payload names, when your interface sends back the digest of what it
        showed, or else the digest of `artefact`. The coordinator refuses
        the decision unless the digest matches the artefact under review.
        """
        if isinstance(decision, Decision):
            if decision.task_id != task_id:
                raise ValueError(
                    f"the decision is on {decision.task_id}; "
                    f"apply_decision was given {task_id}")
            if decision.approved:
                return decision.output
            raise ReviewRejected(task_id, decision.comment,
                                 reviewer=decision.reviewer, state=decision.state)

        if isinstance(decision, str):
            action = decision
            payload: dict[str, Any] = {}
        elif isinstance(decision, dict):
            payload = dict(decision)
            action = payload.pop("action", None)
            if action is None and "diff" in payload:
                action = "override"
            if action is None:
                raise ValueError(
                    "decision payload needs an explicit 'action' "
                    "(approve/reject/override)"
                )
        else:
            raise TypeError(f"unexpected decision payload: {type(decision)}")

        digest = payload.get("approved_artefact_digest") or content_hash(artefact)
        if action == "approve":
            self.approve(task_id, rationale=payload.get("rationale"),
                         approved_artefact_digest=digest)
            return artefact
        if action == "reject":
            rationale = payload.get("rationale", "rejected by reviewer")
            self.reject(task_id, rationale=rationale,
                        category=payload.get("category"),
                        approved_artefact_digest=digest)
            raise ReviewRejected(task_id, rationale, reviewer=self.reviewer)
        if action == "override":
            diff = payload.get("diff")
            if not diff:
                raise ValueError("override requires a 'diff' payload")
            return self.override(
                task_id,
                diff=diff,
                rationale=payload.get("rationale", "reviewer override"),
                tags=payload.get("tags"),
                intent_preserved=payload.get("intent_preserved"),
                approved_artefact_digest=digest,
            )
        raise ValueError(f"unknown decision action: {action!r}")

    def wait_for_decision(self, task_id: str, *,
                          timeout: Optional[float] = None,
                          poll_seconds: float = 2.0) -> Decision:
        """
        Wait for a decision made at a desk on a review this bridge opened,
        and return it. The task is read every `poll_seconds` until it is no
        longer under review. Raises TimeoutError after `timeout` seconds;
        None waits as long as the reviewer takes.
        """
        started = time.monotonic()
        while True:
            view = _read_task(self.client or self.coord, self.workspace, task_id)
            if view is None:
                raise KeyError(f"Unknown task: {task_id}")
            if view.get("state") not in _UNDECIDED:
                return _decision(task_id, view)
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError(f"No decision on {task_id} after {timeout} seconds")
            time.sleep(poll_seconds)

    # ---- audit ----------------------------------------------------

    def audit(self) -> list[dict[str, Any]]:
        """Return the full audit chain for this workspace."""
        env = self._dispatch("audit.read", {"workspace": self.workspace})
        return env["result"]["entries"]

    # ---- internal -------------------------------------------------

    def _join_reviewer(self) -> None:
        if not self._reviewer_joined:
            _safe_dispatch(self.coord, "participant.join", {
                "workspace": self.workspace,
                "from":      self.reviewer,
                "type":      "human",
            })
            self._reviewer_joined = True

    def _dispatch(self, method: str, params: dict) -> dict:
        if self.on_envelope is not None:
            self.on_envelope(method, params)
        envelope = _make_envelope(method, params)
        out = self.coord.dispatch(envelope)
        if "error" in out and out["error"] is not None:
            err = out["error"]
            raise ChapBridgeError(err.get("code", 0), err.get("message", ""))
        return out


# ============================================================
#   hil_review helper
# ============================================================


def hil_review(bridge: ChapBridge, artefact: Any, kind: str,
               input_: Any = None,
               existing_task_id: Optional[str] = None) -> dict[str, Any]:
    """
    Open a review of `artefact` for the bridge's reviewer.

    Call it in the node that produces the draft, and return its result from
    that node. The review is then opened once per draft, and the task id is
    checkpointed in the graph state before the next node interrupts.
    Calling interrupt() in the same node would run this again on resume:
    the draft would be made a second time and a second review opened, and
    the decision would land on a draft the reviewer never saw.

    The task requires review, so only the reviewer's decision completes it.
    The returned dict carries:

        chap_task_id          The CHAP task id; pass it to apply_decision()
                              once the reviewer has decided.
        chap_artefact         The artefact sent for review.
        chap_artefact_digest  Its digest, which the decision carries as
                              approved_artefact_digest.
        chap_kind             The task kind, for logging/routing.

    The integration with LangGraph looks like:

        def draft(state):
            return hil_review(bridge, produce_draft(state), kind="draft_response")

        def review(state):
            decision = interrupt({"task_id": state["chap_task_id"],
                                  "draft": state["chap_artefact"]})
            return {"reply": bridge.apply_decision(
                state["chap_task_id"], decision, state["chap_artefact"])}
    """
    task_id = existing_task_id or bridge.create_task(kind, input_ or {},
                                                     review_required=True)
    bridge.request_review(task_id, artefact)
    return {
        "chap_task_id":         task_id,
        "chap_artefact":        artefact,
        "chap_artefact_digest": content_hash(artefact),
        "chap_kind":            kind,
    }


# ============================================================
#   Errors and helpers
# ============================================================


class ChapBridgeError(RuntimeError):
    """Raised when a CHAP envelope is rejected by the Coordinator."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"CHAP error {code}: {message}")
        self.code = code
        self.message = message


class ReviewRejected(Exception):
    """The reviewer did not approve the draft.

    apply_decision raises it once the rejection is recorded, so a rejected
    draft goes no further. A review that ended another way at a desk,
    abstained, escalated or cancelled, raises it too, since nothing was
    approved. `state` is the task's state: declined after a rejection,
    in_progress when the reviewer asked for another draft.
    """

    def __init__(self, task_id: str, rationale: Optional[str] = None, *,
                 reviewer: Optional[str] = None, state: str = "declined") -> None:
        detail = f": {rationale}" if rationale else ""
        super().__init__(f"{task_id} was not approved ({state}){detail}")
        self.task_id = task_id
        self.rationale = rationale
        self.reviewer = reviewer
        self.state = state


def _safe_dispatch(coord: Coordinator, method: str, params: dict) -> None:
    """
    Dispatch and swallow errors that arise from idempotent re-application
    (re-creating an existing workspace, re-joining an existing participant).
    Other errors propagate.

    Uses a structural check rather than string-matching the error message:
    after a failed call, look at the actual workspace state to decide
    whether the desired effect is already in place.
    """
    envelope = _make_envelope(method, params)
    out = coord.dispatch(envelope)
    if "error" not in out or out["error"] is None:
        return

    # Structural idempotency: if the desired post-condition holds, the
    # error is benign and we proceed. Otherwise propagate.
    if method == "workspace.create":
        ws_id = params.get("workspace")
        if ws_id and ws_id in coord.workspaces:
            return
    elif method == "participant.join":
        ws_id = params.get("workspace")
        uri   = params.get("from")
        ws = coord.workspaces.get(ws_id) if ws_id else None
        if ws is not None and uri in ws.members:
            return

    raise ChapBridgeError(out["error"].get("code", 0),
                          out["error"].get("message", ""))


__all__ = [
    "ChapBridge",
    "ChapBridgeError",
    "Decision",
    "ReviewRejected",
    "hil_review",
]
