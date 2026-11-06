"""
chap-llama-index: put a LlamaIndex Workflow's paused output in front of a
person, or record a decision made elsewhere, as a CHAP decision.

A Workflow step returns an InputRequiredEvent carrying the proposed
output and waits; a HumanResponseEvent answers it. The adapter works at
that pause in one of two ways.

request_response opens a review of the proposed output, waits for the
person's decision, made at a CHAP desk, and returns the HumanResponseEvent
that carries it: `response` is the output as decided, or the reviewer's
note when nothing was approved, and `decision` is approve, override or
reject.

record_decision records a reply your own code made, against the proposed
output, in the reviewer's name:

    decision      CHAP envelope
    --------      ---------------------------
    approve       decide.approve
    override      decide.override  (proposed vs returned, as a diff)
    reject        decide.reject

Workflows events are schemaless and carry no approve/edit/reject signal,
so the decision is passed in explicitly. The returned content, rationale,
tags, and the approver are read off the HumanResponseEvent by convention,
each overridable with a keyword argument.

The bridge depends only on chap-coordinator. llama-index-workflows is
imported only by request_response, to build the event it returns;
record_decision reads events structurally via event.get(...), so the same
code runs against the real types or a test stand-in.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from chap_coordinator import Coordinator


try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-llama-index")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"


def _make_envelope(method: str, params: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id":      f"chap-llama-index-{method}",
        "method":  method,
        "params":  params,
    }


# ---- reading Workflows events without importing them -----------------


def _event_get(event: Any, key: str, default: Any = None) -> Any:
    """Read a field off a Workflows event. Its Event is a DictLikeModel,
    so dynamic fields come through .get(); a plain stand-in falls back to
    attribute access."""
    getter = getattr(event, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(event, key, default)


def _payload(event: Any) -> dict:
    to_dict = getattr(event, "to_dict", None)
    return to_dict() if callable(to_dict) else {}


# ---- RFC 6902 diff (proposed -> returned, as a JSON Patch) -----------


def _escape(token: str) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def _diff(before: Any, after: Any, path: str) -> list[dict]:
    """Patch that turns `before` into `after`. Objects are walked
    key-by-key; arrays and scalars are replaced whole. Keys are sorted so
    the same edit always yields the same patch (it gets hashed into the
    chain). Ported from the TypeScript coordinator's diffJsonPatch."""
    if type(before) is not type(after) or not isinstance(before, dict):
        return [] if before == after else [{"op": "replace", "path": path, "value": after}]
    ops: list[dict] = []
    for key in sorted(before.keys() | after.keys()):
        at = f"{path}/{_escape(key)}"
        if key not in after:
            ops.append({"op": "remove", "path": at})
        elif key not in before:
            ops.append({"op": "add", "path": at, "value": after[key]})
        else:
            ops.extend(_diff(before[key], after[key], at))
    return ops


def _ended_without_approval(decision: Decision) -> str:
    if decision.kind == "reject":
        return "rejected by the reviewer"
    return f"The review ended without an approval ({decision.state})."


def _participant_type(uri: str) -> str:
    scheme = uri.split(":", 1)[0]
    if scheme not in ("human", "agent", "service", "group"):
        raise ValueError(
            "participant URI must use a known scheme "
            f"(human/agent/service/group): {uri!r}"
        )
    return scheme


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
    override, and `output` is then the artefact as decided: the output as
    proposed, or the reviewer's version. Any other ending, a rejection, an
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
        """The reviewer rejected the output and asked the agent for another."""
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
#   ChapHitlBridge
# ============================================================


@dataclass
class ChapHitlBridge:
    """One bridge per workspace. Holds the Coordinator, the workspace it
    writes into, the agent whose step proposed the output, and the default
    approver. Share it across runs; pass a per-decision approver when the
    decision belongs to someone else.

    The bridge joins the agent when it is made. It joins an approver only
    when it records a decision in their name, so a person who decides at a
    desk joins there, with their own key.
    """

    coord:     Coordinator
    workspace: str
    agent:     str
    reviewer:  str
    profiles:  tuple = ("core/1.0", "review/1.0")
    on_envelope: Optional[Callable[[str, dict], None]] = None
    # Reads a task for wait_for_decision: anything with task(task_id)
    # returning the GET /api/tasks/<id> view. Leave it unset with an
    # in-process Coordinator, which is read directly, or with a
    # coordinator client that has task(task_id) itself.
    client: Any = None
    _joined: set = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.reviewer == self.agent:
            raise ValueError(
                "the reviewer cannot be the agent whose output is under review: "
                f"{self.reviewer!r}"
            )
        _safe_dispatch(self.coord, "workspace.create", {
            "workspace": self.workspace,
            "profiles":  list(self.profiles),
        })
        self._join(self.agent)

    # ---- request and wait -----------------------------------------

    async def request_response(self, input_event: Any, *,
                               proposed: Any = None,
                               timeout: Optional[float] = None,
                               poll_seconds: float = 2.0) -> Any:
        """Open a review of the output an InputRequiredEvent proposes, wait
        for the reviewer's decision, and return the HumanResponseEvent that
        answers the pause.

        The review is on a task that requires review, so only a person's
        decision completes it. The event carries `response`, the output as
        decided, or the reviewer's note when nothing was approved;
        `decision`, which is approve, override or reject (any ending without
        an approval reads as reject); `user_name`, the reviewer; and
        `rationale`, `tags` and `chap_task_id`.

        `proposed` defaults to the event's `proposed` field, or else its
        payload. Raises TimeoutError after `timeout` seconds; None waits as
        long as the reviewer takes.
        """
        from workflows.events import HumanResponseEvent

        if not self.reviewer.startswith("human:"):
            raise ValueError(
                "a review that waits for a person needs a 'human:' reviewer, "
                f"got {self.reviewer!r}"
            )
        if proposed is None:
            proposed = _event_get(input_event, "proposed") or _payload(input_event)
        task_id = self._submit(proposed, self.reviewer, review_required=True)
        decision = await self.wait_for_decision(task_id, timeout=timeout,
                                                poll_seconds=poll_seconds)
        if decision.approved:
            kind, response = decision.kind, decision.output
        else:
            kind = "reject"
            response = decision.comment or _ended_without_approval(decision)
        return HumanResponseEvent(
            response=response,
            decision=kind,
            user_name=decision.reviewer,
            rationale=decision.comment,
            tags=list(decision.tags),
            chap_task_id=task_id,
        )

    async def wait_for_decision(self, task_id: str, *,
                                timeout: Optional[float] = None,
                                poll_seconds: float = 2.0) -> Decision:
        """Wait for the reviewer's decision on a review this bridge opened,
        and return it. The task is read every `poll_seconds` until it is no
        longer under review. Raises TimeoutError after `timeout` seconds;
        None waits as long as the reviewer takes."""
        started = time.monotonic()
        while True:
            view = _read_task(self.client or self.coord, self.workspace, task_id)
            if view is None:
                raise KeyError(f"Unknown task: {task_id}")
            if view.get("state") not in _UNDECIDED:
                return _decision(task_id, view)
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError(f"No decision on {task_id} after {timeout} seconds")
            await asyncio.sleep(poll_seconds)

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
        """Record one human decision and return the task id.

        `input_event` is the InputRequiredEvent that paused the step;
        `response_event` is the HumanResponseEvent that answered it.
        `decision` is required and authoritative -- Workflows events carry
        no approve/edit/reject signal to infer from. `proposed`, `returned`,
        `rationale`, `tags`, `intent_preserved`, and `approver` default to
        fields read off the events and can be overridden per call.

        The decision is recorded in the approver's name, and the approver
        joins the workspace first if the bridge has not joined them yet.
        """
        approver = approver or _event_get(response_event, "user_name") or self.reviewer
        self._join(approver)

        if proposed is None:
            proposed = _event_get(input_event, "proposed") or _payload(input_event)
        if rationale is None:
            rationale = _event_get(response_event, "rationale")
        if tags is None:
            tags = _event_get(response_event, "tags")

        task_id = self._submit(proposed, approver)

        if decision == "approve":
            self._decide("decide.approve", approver, task_id, rationale, tags)
        elif decision == "override":
            if returned is None:
                returned = _event_get(response_event, "response")
            if diff is None:
                if returned is None:
                    raise ValueError("an override needs `returned` content (or a `diff`)")
                diff = _diff(proposed, returned, "")
            if not diff:
                # The edit changed nothing; record the approve it really is.
                self._decide("decide.approve", approver, task_id, rationale, tags)
                return task_id
            if intent_preserved is None:
                intent_preserved = _event_get(response_event, "intent_preserved")
            self._dispatch("decide.override", {
                "workspace": self.workspace,
                "from":      approver,
                "task_id":   task_id,
                "diff":      diff,
                "rationale": rationale or "reviewer edited the output",
                "tags":      tags or [],
                "intent_preserved": True if intent_preserved is None else intent_preserved,
            })
        elif decision == "reject":
            note = rationale or _event_get(response_event, "response")
            self._decide("decide.reject", approver, task_id, note, tags)
        else:
            raise ValueError(
                f"decision must be 'approve', 'override', or 'reject': {decision!r}"
            )
        return task_id

    def audit(self) -> list[dict]:
        env = self._dispatch("audit.read", {"workspace": self.workspace})
        return env["result"]["entries"]

    # ---- internal -------------------------------------------------

    def _submit(self, artefact: Any, approver: str,
                review_required: bool = False) -> str:
        """Open the task and send the proposed output for review, the
        agent's half of the handshake. A task that requires review waits
        under review until a person decides it; otherwise the output is
        first recorded as the task's output."""
        params: dict = {
            "workspace": self.workspace,
            "from":      self.agent,
            "assignee":  self.agent,
            "kind":      "workflow_output",
            "input":     artefact,
        }
        if review_required:
            params["review_required"] = True
        env = self._dispatch("task.create", params)
        task_id = env["result"]["task_id"]
        if not review_required:
            self._dispatch("task.complete", {
                "workspace": self.workspace,
                "from":      self.agent,
                "task_id":   task_id,
                "output":    artefact,
            })
        self._dispatch("review.request", {
            "workspace": self.workspace,
            "from":      self.agent,
            "task_id":   task_id,
            "artefact":  artefact,
            "to":        approver,
        })
        return task_id

    def _decide(self, method: str, approver: str, task_id: str,
                comment: Optional[str], tags: Optional[list[str]]) -> None:
        # decide.approve / decide.reject record the reviewer's note as
        # `comment` (decide.override is the one that takes `rationale`).
        params: dict = {
            "workspace": self.workspace,
            "from":      approver,
            "task_id":   task_id,
        }
        if comment:
            params["comment"] = comment
        if tags:
            params["tags"] = tags
        self._dispatch(method, params)

    def _join(self, uri: str) -> None:
        if uri in self._joined:
            return
        _safe_dispatch(self.coord, "participant.join", {
            "workspace": self.workspace,
            "from":      uri,
            "type":      _participant_type(uri),
        })
        self._joined.add(uri)

    def _dispatch(self, method: str, params: dict) -> dict:
        if self.on_envelope is not None:
            self.on_envelope(method, params)
        out = self.coord.dispatch(_make_envelope(method, params))
        if out.get("error"):
            err = out["error"]
            raise ChapBridgeError(err.get("code", 0), err.get("message", ""))
        return out


# ============================================================
#   Errors and helpers
# ============================================================


class ChapBridgeError(RuntimeError):
    """Raised when the Coordinator rejects an envelope."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"CHAP error {code}: {message}")
        self.code = code
        self.message = message


def _safe_dispatch(coord: Coordinator, method: str, params: dict) -> None:
    """Dispatch, tolerating the errors that come from re-applying an
    idempotent setup step (re-creating a workspace, re-joining a member).
    Decide by checking the resulting state, not by matching the message;
    anything else propagates."""
    out = coord.dispatch(_make_envelope(method, params))
    if not out.get("error"):
        return

    if method == "workspace.create":
        ws_id = params.get("workspace")
        if ws_id and ws_id in coord.workspaces:
            return
    elif method == "participant.join":
        ws = coord.workspaces.get(params.get("workspace"))
        if ws is not None and params.get("from") in ws.members:
            return

    raise ChapBridgeError(out["error"].get("code", 0),
                          out["error"].get("message", ""))


__all__ = [
    "ChapHitlBridge",
    "ChapBridgeError",
    "Decision",
]
