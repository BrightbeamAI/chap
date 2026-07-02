"""
chap-ag2: record a CHAP human-decision at an AG2 (AutoGen) human-input
turn.

AG2 collects human input through `get_human_input` on a UserProxyAgent /
ConversableAgent running with `human_input_mode="ALWAYS"`. At that point
the message under review is the agent's last message and the reply is the
human's text. This adapter records that turn:

    decision      CHAP envelope
    --------      ---------------------------
    approve       decide.approve
    override      decide.override  (message vs reply, as a diff)
    reject        decide.reject

AG2's turn is a weak signal -- the same loop carries plain dialogue -- so
`decision` is authoritative. The only inference the adapter makes is that
an empty reply means "use the agent's output", i.e. approve. A non-empty
reply with no explicit decision records nothing: ending a chat is not a
rejection, and dialogue is not an edit.

The bridge depends only on chap-coordinator. AG2 is never imported here:
the message and reply are read structurally, so the same code runs
against real AG2 objects or plain values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from chap_coordinator import Coordinator


__version__ = "0.2.6"


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
    return scheme if scheme in ("human", "agent", "service", "group") else "human"


# ============================================================
#   ChapTurnBridge
# ============================================================


@dataclass
class ChapTurnBridge:
    """One bridge per workspace. Holds the Coordinator, the workspace it
    writes into, the agent whose message is under review, and the default
    approver. Share it across a conversation; pass a per-turn approver when
    the decision belongs to someone else.
    """

    coord:     Coordinator
    workspace: str
    agent:     str
    reviewer:  str
    profiles:  tuple = ("core/1.0", "review/1.0")
    on_envelope: Optional[Callable[[str, dict], None]] = None

    def __post_init__(self) -> None:
        _safe_dispatch(self.coord, "workspace.create", {
            "workspace": self.workspace,
            "profiles":  list(self.profiles),
        })
        self._join(self.agent)
        self._join(self.reviewer)

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
        """
        if decision is None:
            if _is_empty(reply):
                decision = "approve"
            else:
                return None

        approver = approver or self.reviewer
        if approver != self.reviewer:
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

    def _submit(self, artefact: Any, approver: str) -> str:
        """Record the message as the agent's task output and send it for
        review -- the agent's half of the handshake."""
        env = self._dispatch("task.create", {
            "workspace": self.workspace,
            "from":      self.agent,
            "assignee":  self.agent,
            "kind":      "agent_message",
            "input":     artefact,
        })
        task_id = env["result"]["task_id"]
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
        _safe_dispatch(self.coord, "participant.join", {
            "workspace": self.workspace,
            "from":      uri,
            "type":      _participant_type(uri),
        })

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
    "ChapTurnBridge",
    "ChapBridgeError",
]
