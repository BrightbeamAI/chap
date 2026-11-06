"""
chap-google-adk: put a Google ADK tool call that needs confirmation in
front of a person, or record a confirmation made elsewhere, as a CHAP
decision.

ADK gates a tool with ``FunctionTool(fn, require_confirmation=True)`` (or a
tool calling ``tool_context.request_confirmation(...)``). The run pauses and
surfaces the paused call plus a ``ToolConfirmation``; the app resumes by
returning a ``ToolConfirmation(confirmed=..., payload=...)``. The adapter
works at that resolution point in one of two ways, and in both the
confirmation is what ADK carries out:

    CHAP decision                 ToolConfirmation
    ---------------------------   ----------------------------------------
    decide.approve                confirmed=True
    decide.override (the args)   confirmed=True, payload={"args": {...}}
    decide.reject                 confirmed=False

request_confirmation opens a review of the paused call, waits for the
person's decision, made at a CHAP desk, and returns the ToolConfirmation
to resume with. record_decision records a confirmation your own code made,
in the reviewer's name, and refuses one ADK would not carry out as
recorded.

ADK runs a confirmed call with the arguments the model proposed. An edit
reaches the tool through the confirmation's payload: give the agent
``before_tool_callback=apply_reviewed_args``, which runs a confirmed call
with the arguments its payload carries as ``args``. A payload with no
``args`` is the tool's own data, as when a leave tool asks for
``approved_days``, and is left alone.

The bridge depends only on chap-coordinator. google-adk is imported only by
request_confirmation, to build the ToolConfirmation it returns;
record_decision reads the call and confirmation structurally, so the same
code runs against real ADK objects or plain values.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from chap_coordinator import Coordinator, PatchError, apply_json_patch


try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-google-adk")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"


def _make_envelope(method: str, params: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id":      f"chap-google-adk-{method}",
        "method":  method,
        "params":  params,
    }


# ---- reading ADK objects without importing them ----------------------


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field whether ``obj`` is an ADK model (attribute) or a plain
    dict (the shape ADK also serialises confirmations into)."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _args(tool_call: Any) -> dict:
    args = _attr(tool_call, "args")
    return dict(args) if args else {}


def _confirmed(confirmation: Any) -> bool:
    return bool(_attr(confirmation, "confirmed", False))


def _payload_args(confirmation: Any) -> Optional[dict]:
    """The arguments a confirmation carries for the tool: its payload's
    `args`, when the payload is an object holding one, or else None."""
    payload = _attr(confirmation, "payload")
    args = payload.get("args") if isinstance(payload, dict) else None
    return dict(args) if isinstance(args, dict) else None


def _artefact(tool_call: Any) -> dict:
    """The paused tool call, as the artefact under review."""
    return {
        "tool":         _attr(tool_call, "name"),
        "args":         _args(tool_call),
        "tool_call_id": _attr(tool_call, "id"),
    }


def apply_reviewed_args(tool: Any, args: dict, tool_context: Any) -> None:
    """An ADK before_tool_callback that runs a confirmed call with the
    reviewer's arguments.

    ADK runs a confirmed call with the arguments the model proposed. When
    the ToolConfirmation that resumed the call is confirmed and its payload
    carries `args`, this replaces the call's arguments with those, before
    ADK validates them and calls the tool. A call with no such payload runs
    as proposed. It returns None, so the tool runs:

        LlmAgent(..., before_tool_callback=apply_reviewed_args)
    """
    confirmation = getattr(tool_context, "tool_confirmation", None)
    edited = _payload_args(confirmation) if confirmation is not None else None
    if edited is not None and _confirmed(confirmation):
        args.clear()
        args.update(edited)
    return None


# ---- RFC 6902 diff (original args -> edited args, as a JSON Patch) ----


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
#   ChapConfirmationBridge
# ============================================================


@dataclass
class ChapConfirmationBridge:
    """One bridge per workspace. Holds the Coordinator, the workspace it
    writes into, the agent whose tool call is under review, and the default
    approver. Share it across a run; pass a per-decision approver when the
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
                "the reviewer cannot be the agent whose calls are under review: "
                f"{self.reviewer!r}"
            )
        _safe_dispatch(self.coord, "workspace.create", {
            "workspace": self.workspace,
            "profiles":  list(self.profiles),
        })
        self._join(self.agent)

    # ---- request and wait -----------------------------------------

    async def request_confirmation(self, tool_call: Any, *,
                                   timeout: Optional[float] = None,
                                   poll_seconds: float = 2.0) -> Any:
        """Open a review of a paused tool call, wait for the reviewer's
        decision, and return the ToolConfirmation to resume the run with.

        `tool_call` is the call ADK paused: the `originalFunctionCall` of
        its adk_request_confirmation call, or anything with its name, args
        and id. The review is on a task that requires review, so only a
        person's decision completes it. An approval confirms the call, and
        an override confirms it with payload={"args": ...}, the reviewer's
        arguments, which apply_reviewed_args hands the tool. Anything else
        is not confirmed, and neither is an edit to anything but the
        arguments, which the run cannot apply.

        Raises TimeoutError after `timeout` seconds; None waits as long as
        the reviewer takes.
        """
        from google.adk.tools.tool_confirmation import ToolConfirmation

        if not self.reviewer.startswith("human:"):
            raise ValueError(
                "a review that waits for a person needs a 'human:' reviewer, "
                f"got {self.reviewer!r}"
            )
        artefact = _artefact(tool_call)
        task_id = self._submit(artefact, self.reviewer, review_required=True)
        decision = await self.wait_for_decision(task_id, timeout=timeout,
                                                poll_seconds=poll_seconds)
        output = decision.output if isinstance(decision.output, dict) else {}
        if not decision.approved or any(output.get(key) != artefact[key]
                                        for key in ("tool", "tool_call_id")):
            return ToolConfirmation(confirmed=False)
        edited = output.get("args")
        if isinstance(edited, dict) and edited != artefact["args"]:
            return ToolConfirmation(confirmed=True, payload={"args": edited})
        return ToolConfirmation(confirmed=True)

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

    def record_decision(self, tool_call: Any, confirmation: Any, *,
                        decision: Optional[str] = None,
                        returned: Optional[dict] = None,
                        diff: Optional[list[dict]] = None,
                        approver: Optional[str] = None,
                        rationale: Optional[str] = None,
                        tags: Optional[list[str]] = None,
                        intent_preserved: Optional[bool] = None) -> str:
        """Record one resolved confirmation and return the task id.

        `tool_call` is the paused call (its name + args are the artefact);
        `confirmation` is the ToolConfirmation the run resumes with, and
        what it will make ADK do is what is recorded: not confirmed is a
        rejection; confirmed with payload={"args": ...} that differ from
        the call's is an override, to those arguments; confirmed otherwise
        is an approval.

        `decision`, `returned` and `diff` may restate that, and a value
        that disagrees with the confirmation raises ValueError, with
        nothing recorded. In particular an override needs the edited
        arguments in the payload, since that is the only way they reach
        the tool (through apply_reviewed_args); an edit passed only as
        `returned` would be recorded and never run.
        """
        args = _args(tool_call)
        artefact = _artefact(tool_call)
        edited = _payload_args(confirmation) if _confirmed(confirmation) else None
        carried = ("reject" if not _confirmed(confirmation)
                   else "override" if edited is not None and edited != args
                   else "approve")
        if decision not in (None, "approve", "override", "reject"):
            raise ValueError(
                f"decision must be 'approve', 'override', or 'reject': {decision!r}"
            )
        if decision == "override" and edited is None:
            raise ValueError(
                "an override is recorded only when ADK will run it: resume with "
                "ToolConfirmation(confirmed=True, payload={'args': <the edited "
                "args>}) and give the agent before_tool_callback=apply_reviewed_args"
            )
        # An override whose arguments equal the call's changes nothing, and
        # is recorded as the approval it is.
        if decision is not None and decision != carried and (decision, carried) != (
                "override", "approve"):
            raise ValueError(
                f"the confirmation makes this call {carried!r}, so it cannot be "
                f"recorded as {decision!r}"
            )
        if returned is not None and returned != (edited if carried == "override" else args):
            raise ValueError(
                "`returned` differs from the arguments the confirmation runs the call with"
            )
        if diff is not None and carried == "override":
            try:
                patched = apply_json_patch(artefact, diff)
            except PatchError as exc:
                raise ValueError(f"`diff` does not apply to the call: {exc}") from exc
            if patched != {**artefact, "args": edited}:
                raise ValueError(
                    "`diff` does not give the arguments the confirmation runs the call with"
                )

        approver = approver or self.reviewer
        self._join(approver)
        task_id = self._submit(artefact, approver)

        if carried == "approve":
            self._decide("decide.approve", approver, task_id, rationale, tags)
        elif carried == "override":
            self._dispatch("decide.override", {
                "workspace": self.workspace,
                "from":      approver,
                "task_id":   task_id,
                "diff":      diff if diff is not None else _diff(args, edited, "/args"),
                "rationale": rationale or "reviewer edited the tool arguments",
                "tags":      tags or [],
                "intent_preserved": True if intent_preserved is None else intent_preserved,
            })
        else:
            self._decide("decide.reject", approver, task_id, rationale, tags)
        return task_id

    def audit(self) -> list[dict]:
        env = self._dispatch("audit.read", {"workspace": self.workspace})
        return env["result"]["entries"]

    # ---- internal -------------------------------------------------

    def _submit(self, artefact: dict, approver: str,
                review_required: bool = False) -> str:
        """Open the task and send the tool call for review, the agent's half
        of the handshake. A task that requires review waits under review
        until a person decides it; otherwise the call is first recorded as
        the task's output."""
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
    "ChapConfirmationBridge",
    "ChapBridgeError",
    "Decision",
    "apply_reviewed_args",
]
