"""
chap-ag2: put an AG2 (AutoGen) agent's message in front of a person at a
human-input turn, or record a decision made elsewhere, as a CHAP decision.

AG2 collects human input through `get_human_input` on a UserProxyAgent /
ConversableAgent running with `human_input_mode="ALWAYS"`. At that point
the message under review is the agent's last message and the reply is the
human's text. The adapter works at that turn in one of two ways.

request_reply opens a review of the message, waits for the person's
decision, made at a CHAP desk, and returns the reply that carries it out:

    CHAP decision                         reply to AG2
    ---------------------------           -----------------------------
    decide.approve                        "" (the chat carries on)
    decide.override                       the reviewer's version
    decide.reject, asking for a revision  the reviewer's note
    any other ending                      raises ReviewRejected

record_turn records a reply your own code took, in the reviewer's name:

    decision      CHAP envelope
    --------      ---------------------------
    approve       decide.approve
    override      decide.override  (message vs reply, as a diff)
    reject        decide.reject

AG2's turn is a weak signal, since the same loop carries plain dialogue,
so `decision` is authoritative. The only inference the adapter makes is
that an empty reply means "use the agent's output", i.e. approve. A
non-empty reply with no explicit decision records nothing: ending a chat
is not a rejection, and dialogue is not an edit.

The bridge depends only on chap-coordinator. AG2 is never imported here:
the message and reply are read structurally, so the same code runs
against real AG2 objects or plain values.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from chap_coordinator import Coordinator


try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-ag2")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"


def _make_envelope(method: str, params: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id":      f"chap-ag2-{method}",
        "method":  method,
        "params":  params,
    }


def _content(message: Any) -> Any:
    """The reviewable substance of an AG2 message: its content if the
    message is a dict, else the message itself."""
    if isinstance(message, dict):
        return message.get("content", message)
    return message


def _is_empty(reply: Any) -> bool:
    return not (reply or "").strip() if isinstance(reply, str) else not reply


# ---- RFC 6902 diff (message -> reply, as a JSON Patch) ---------------


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
    override, and `output` is then the artefact as decided: the message as
    the agent wrote it, or the reviewer's version. Any other ending, a
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
        """The reviewer rejected the message and asked the agent for another."""
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
#   ChapTurnBridge
# ============================================================


@dataclass
class ChapTurnBridge:
    """One bridge per workspace. Holds the Coordinator, the workspace it
    writes into, the agent whose message is under review, and the default
    approver. Share it across a conversation; pass a per-turn approver when
    the decision belongs to someone else.

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
    # in-process Coordinator, which is read directly.
    client: Any = None
    _joined: set = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.reviewer == self.agent:
            raise ValueError(
                "the reviewer cannot be the agent whose messages are under review: "
                f"{self.reviewer!r}"
            )
        _safe_dispatch(self.coord, "workspace.create", {
            "workspace": self.workspace,
            "profiles":  list(self.profiles),
        })
        self._join(self.agent)

    # ---- request and wait -----------------------------------------

    def request_reply(self, message: Any, *,
                      timeout: Optional[float] = None,
                      poll_seconds: float = 2.0) -> str:
        """Open a review of the agent's message, wait for the reviewer's
        decision, and return the reply for get_human_input to give AG2.

        The review is on a task that requires review, so only a person's
        decision completes it. An approval returns "", and AG2 carries on
        with its auto-reply. An override returns the reviewer's version of
        the message, as text. A rejection that asks for a revision returns
        the reviewer's note, for the agent to act on. Anything else raises
        ReviewRejected, which ends the chat: its last message was not
        approved, so nothing should use the chat's result as if it were.

        Raises TimeoutError after `timeout` seconds; None waits as long as
        the reviewer takes.
        """
        if not self.reviewer.startswith("human:"):
            raise ValueError(
                "a review that waits for a person needs a 'human:' reviewer, "
                f"got {self.reviewer!r}"
            )
        task_id = self._submit(_content(message), self.reviewer, review_required=True)
        decision = self.wait_for_decision(task_id, timeout=timeout,
                                          poll_seconds=poll_seconds)
        if decision.kind == "approve" and decision.approved:
            return ""
        if decision.approved:
            output = decision.output
            return output if isinstance(output, str) else json.dumps(output)
        if decision.revision_requested:
            return decision.comment or "The reviewer asked for another draft."
        raise ReviewRejected(task_id, decision.comment,
                             reviewer=decision.reviewer, state=decision.state)

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

    def record_turn(self, message: Any, reply: Any, *,
                   decision: Optional[str] = None,
                   approver: Optional[str] = None,
                   rationale: Optional[str] = None,
                   tags: Optional[list[str]] = None,
                   intent_preserved: Optional[bool] = None) -> Optional[str]:
        """Record one human turn and return the task id, or None if the
        turn is not a decision.

        `message` is the agent message the human responded to; `reply` is
        their text. `decision` is authoritative. When it is omitted, only
        an empty reply is inferred (approve); any other reply records
        nothing, because AG2's loop also carries plain dialogue.

        The decision is recorded in the approver's name, and the approver
        joins the workspace first if the bridge has not joined them yet.
        """
        if decision is None:
            if _is_empty(reply):
                decision = "approve"
            else:
                return None

        approver = approver or self.reviewer
        self._join(approver)

        proposed = _content(message)
        task_id = self._submit(proposed, approver)

        if decision == "approve":
            self._decide("decide.approve", approver, task_id, rationale, tags)
        elif decision == "override":
            diff = _diff(proposed, reply, "")
            if not diff:
                self._decide("decide.approve", approver, task_id, rationale, tags)
                return task_id
            self._dispatch("decide.override", {
                "workspace": self.workspace,
                "from":      approver,
                "task_id":   task_id,
                "diff":      diff,
                "rationale": rationale or "reviewer edited the message",
                "tags":      tags or [],
                "intent_preserved": True if intent_preserved is None else intent_preserved,
            })
        elif decision == "reject":
            note = rationale or (reply if not _is_empty(reply) else None)
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
        """Open the task and send the message for review, the agent's half
        of the handshake. A task that requires review waits under review
        until a person decides it; otherwise the message is first recorded
        as the task's output."""
        params: dict = {
            "workspace": self.workspace,
            "from":      self.agent,
            "assignee":  self.agent,
            "kind":      "agent_message",
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


class ReviewRejected(Exception):
    """The reviewer did not approve the agent's message.

    request_reply raises it from get_human_input, which ends the chat, so a
    rejected message is never taken for an approved one. `state` is the
    task's state: declined after a rejection, or the state an abstention,
    an escalation or a cancellation left it in.
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
    "ChapTurnBridge",
    "ChapBridgeError",
    "Decision",
    "ReviewRejected",
]
