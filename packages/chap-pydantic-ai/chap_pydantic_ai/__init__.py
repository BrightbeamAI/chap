"""
chap-pydantic-ai: put a Pydantic AI tool approval in front of a person, or
record one made elsewhere, as a CHAP decision.

Pydantic AI gates a tool with requires_approval=True (or an
ApprovalRequiredToolset). The run then ends with a DeferredToolRequests
output whose .approvals holds the pending ToolCallParts. The caller
resolves each one into DeferredToolResults.approvals and feeds it back
via deferred_tool_results=. The adapter works at that resolution point in
one of two ways.

request_results opens a review of each pending call and waits for the
person's decision, made at a CHAP desk, then returns the
DeferredToolResults that carry it out:

    CHAP decision                     Pydantic AI resolution
    ---------------------------       ------------------------
    decide.approve                    ToolApproved()
    decide.override (the args)        ToolApproved(override_args=...)
    decide.reject, or no approval     ToolDenied(message=...)

record_results records a resolution your own code made, in the reviewer's
name:

    Pydantic AI resolution            CHAP envelope
    ------------------------          ---------------------------
    True / ToolApproved()             decide.approve
    ToolApproved(override_args=...)   decide.override  (diff of the args)
    False / ToolDenied(message=...)   decide.reject

The proposed tool call (tool name, args, tool_call_id) is the artefact
under review; the resolution is the human's decision on it. Every
envelope lands in the workspace audit chain, hash-linked and replayable.

The bridge depends only on chap-coordinator. Pydantic AI is imported only
by request_results, to build the results it returns; record_results reads
the resolution objects structurally, so the same code runs against the
real types or a test stand-in.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from uuid import uuid4

from chap_coordinator import Coordinator


try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-pydantic-ai")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"


def _make_envelope(method: str, params: dict) -> dict:
    """Build a JSON-RPC 2.0 envelope with an id of its own. A client that
    signs what the bridge sends then signs two identical calls as two
    requests, and the coordinator, which takes a signed request once,
    takes each of them."""
    return {
        "jsonrpc": "2.0",
        "id":      f"chap-pydantic-ai-{method}-{uuid4().hex}",
        "method":  method,
        "params":  params,
    }


# ---- reading Pydantic AI objects without importing them --------------


def _arguments(call: Any) -> dict:
    """Pending args as a dict. ToolCallPart.args is str | dict | None;
    args_as_dict() normalises it, so a model that emitted JSON text and
    one that emitted a dict produce the same record."""
    as_dict = getattr(call, "args_as_dict", None)
    if callable(as_dict):
        return as_dict()
    args = getattr(call, "args", None)
    if isinstance(args, str):
        return json.loads(args) if args.strip() else {}
    return dict(args) if args else {}


def _override_args(resolution: Any) -> Optional[dict]:
    """The edited args on a ToolApproved, or None. A bare True and a
    ToolDenied have no such attribute, so they read as None."""
    return getattr(resolution, "override_args", None)


def _is_approved(resolution: Any) -> bool:
    if resolution is True:
        return True
    if resolution is False:
        return False
    return hasattr(resolution, "override_args")  # ToolApproved


def _denial_message(resolution: Any) -> str:
    return getattr(resolution, "message", None) or "tool call denied by reviewer"


def _artefact(call: Any) -> dict:
    """The proposed tool call, as the artefact under review."""
    return {
        "tool":         getattr(call, "tool_name", None),
        "args":         _arguments(call),
        "tool_call_id": getattr(call, "tool_call_id", None),
    }


def _ended_without_approval(decision: "Decision") -> str:
    if decision.kind == "reject":
        return "tool call denied by reviewer"
    return f"The review ended without an approval ({decision.state}), so the call was not made."


def _participant_type(uri: str) -> str:
    """CHAP participant type from the URI scheme: human:alice -> human,
    agent:bot -> agent. The type then matches the identity instead of
    being assumed."""
    scheme = uri.split(":", 1)[0]
    if scheme not in ("human", "agent", "service", "group"):
        raise ValueError(
            "participant URI must use a known scheme "
            f"(human/agent/service/group): {uri!r}"
        )
    return scheme


# ---- RFC 6902 diff (the args edit, as a JSON Patch) ------------------


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
    override, and `output` is then the artefact as decided: the tool call
    as proposed, or with the reviewer's edit. Any other ending, a
    rejection, an abstention, or a review escalated or cancelled, approves
    nothing, and `output` is None.
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
        """The reviewer rejected the call and asked the agent for another."""
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
#   ChapApprovalBridge
# ============================================================


@dataclass
class ChapApprovalBridge:
    """One bridge per workspace. Holds the Coordinator, the workspace it
    writes into, the agent that proposed the tool calls, and the default
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
    # Lets tests / consumers observe every envelope before it reaches the
    # Coordinator. Receives (method, params).
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
                "the reviewer cannot be the agent whose calls are under review: "
                f"{self.reviewer!r}"
            )
        _safe_dispatch(self.coord, "workspace.create", {
            "workspace": self.workspace,
            "profiles":  list(self.profiles),
        })
        self._join(self.agent)

    # ---- request and wait -----------------------------------------

    def request_results(self, requests: Any, *,
                        timeout: Optional[float] = None,
                        poll_seconds: float = 2.0) -> Any:
        """Open a review of every pending approval in a DeferredToolRequests,
        wait for the reviewer's decisions, and return the
        DeferredToolResults that carry them out.

        Each call is reviewed on its own task, which requires review, so
        only a person's decision completes it. An approval runs the call
        as proposed and an override runs it with the reviewer's arguments.
        Anything else denies it, with the reviewer's note as the message
        the model reads; so does an edit to anything but the arguments,
        which the run cannot apply. Each result's metadata names the CHAP
        task and the reviewer, for the tool to read as
        ctx.tool_call_metadata.

        The reviews are all opened before the first wait, so the reviewer
        sees them together. Raises TimeoutError when they are not all
        decided within `timeout` seconds; None waits as long as the
        reviewer takes. The reviews stay open in the desk.
        """
        from pydantic_ai import DeferredToolResults, ToolApproved, ToolDenied

        if not self.reviewer.startswith("human:"):
            raise ValueError(
                "a review that waits for a person needs a 'human:' reviewer, "
                f"got {self.reviewer!r}"
            )
        opened = [(call, self._submit(_artefact(call), self.reviewer, review_required=True))
                  for call in requests.approvals]
        deadline = None if timeout is None else time.monotonic() + timeout
        results = DeferredToolResults()
        for call, task_id in opened:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            decision = self.wait_for_decision(task_id, timeout=remaining,
                                              poll_seconds=poll_seconds)
            artefact = _artefact(call)
            output = decision.output if isinstance(decision.output, dict) else {}
            if decision.approved and all(output.get(key) == artefact[key]
                                         for key in ("tool", "tool_call_id")):
                edited = output.get("args")
                results.approvals[artefact["tool_call_id"]] = (
                    ToolApproved(override_args=edited) if edited != artefact["args"]
                    else ToolApproved())
            elif decision.approved:
                results.approvals[artefact["tool_call_id"]] = ToolDenied(
                    "The reviewer's edit changed the call itself, which this run "
                    "cannot apply, so the call was not made.")
            else:
                results.approvals[artefact["tool_call_id"]] = ToolDenied(
                    decision.comment or _ended_without_approval(decision))
            results.metadata[artefact["tool_call_id"]] = {
                "chap_task_id": task_id, "reviewer": decision.reviewer,
            }
        return results

    def wait_for_decision(self, task_id: str, *,
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
            time.sleep(poll_seconds)

    # ---- record a decision made elsewhere --------------------------

    def record_decision(self, call: Any, resolution: Any, *,
                        approver: Optional[str] = None,
                        rationale: Optional[str] = None,
                        tags: Optional[list[str]] = None,
                        intent_preserved: Optional[bool] = None) -> str:
        """Record one resolved approval and return the task id.

        `call` is the pending ToolCallPart; `resolution` is what the
        caller put in DeferredToolResults.approvals (bool, ToolApproved,
        or ToolDenied). The keyword arguments are the human's signal that
        the resolution object can't carry on its own; a resolver lifts
        them from DeferredToolResults.metadata[tool_call_id].

        The decision is recorded in the approver's name, and the approver
        joins the workspace first if the bridge has not joined them yet.
        """
        approver = approver or self.reviewer
        self._join(approver)

        artefact = _artefact(call)
        args = artefact["args"]
        task_id = self._submit(artefact, approver)

        override = _override_args(resolution)
        diff = _diff(args, override, "/args") if override is not None else None
        if diff:
            self._dispatch("decide.override", {
                "workspace": self.workspace,
                "from":      approver,
                "task_id":   task_id,
                "diff":      diff,
                "rationale": rationale or "reviewer edited tool arguments",
                "tags":      tags or [],
                # Editing args is refining by default, but the reviewer can
                # say otherwise: a different decision in disguise is not.
                "intent_preserved": True if intent_preserved is None else intent_preserved,
            })
        elif _is_approved(resolution):
            self._decide("decide.approve", approver, task_id, rationale, tags)
        else:
            self._decide("decide.reject", approver, task_id,
                         rationale or _denial_message(resolution), tags)
        return task_id

    def record_results(self, requests: Any, results: Any) -> list[str]:
        """Record every approval in a DeferredToolResults against the
        DeferredToolRequests it answers. Rationale, tags, approver, and
        intent_preserved are read per call from results.metadata. Returns
        the task ids in request order.
        """
        meta = getattr(results, "metadata", None) or {}
        task_ids = []
        for call in requests.approvals:
            cid = getattr(call, "tool_call_id", None)
            if cid not in results.approvals:
                raise ValueError(f"no resolution for pending approval {cid!r}")
            signal = meta.get(cid, {})
            task_ids.append(self.record_decision(
                call, results.approvals[cid],
                approver=signal.get("approver"),
                rationale=signal.get("rationale"),
                tags=signal.get("tags"),
                intent_preserved=signal.get("intent_preserved"),
            ))
        return task_ids

    def audit(self) -> list[dict]:
        env = self._dispatch("audit.read", {"workspace": self.workspace})
        return env["result"]["entries"]

    # ---- internal -------------------------------------------------

    def _submit(self, artefact: dict, approver: str,
                review_required: bool = False) -> str:
        """Open the task and send the agent's tool call for review, the
        agent's half of the handshake. A task that requires review waits
        under review until a person decides it; otherwise the call is first
        recorded as the task's output."""
        params: dict = {
            "workspace": self.workspace,
            "from":      self.agent,
            "assignee":  self.agent,
            "kind":      artefact["tool"] or "tool_call",
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
    "ChapApprovalBridge",
    "ChapBridgeError",
    "Decision",
]
