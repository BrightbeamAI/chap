"""
chap-google-adk: record a CHAP human-decision when a Google ADK run pauses
for tool confirmation.

ADK gates a tool with ``FunctionTool(fn, require_confirmation=True)`` (or a
tool calling ``tool_context.request_confirmation(...)``). The run pauses and
surfaces the paused call plus a ``ToolConfirmation``; the app resumes by
returning a ``ToolConfirmation(confirmed=..., payload=...)``. This adapter
records the decision at that resolution point:

    decision      CHAP envelope
    --------      ---------------------------
    approve       decide.approve
    override      decide.override  (original args vs the edited value)
    reject        decide.reject

The confirmation's ``confirmed`` flag is a clean approve/reject signal, so
approve/reject are derived from it. Override is never inferred: ADK's
``payload`` is a separate, tool-defined object (a leave tool asks for
``approved_days``, not a modified ``days``), so treating it as the edited
args would record a change the human never made. An edit is recorded only
when the caller passes it explicitly via ``returned`` (the edited args) or
``diff``.

The bridge depends only on chap-coordinator. google-adk is never imported
here: the call and confirmation are read structurally, so the same code runs
against real ADK objects or plain values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from chap_coordinator import Coordinator


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
    return scheme if scheme in ("human", "agent", "service", "group") else "human"


# ============================================================
#   ChapConfirmationBridge
# ============================================================


@dataclass
class ChapConfirmationBridge:
    """One bridge per workspace. Holds the Coordinator, the workspace it
    writes into, the agent whose tool call is under review, and the default
    approver. Share it across a run; pass a per-decision approver when the
    decision belongs to someone else.
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
        `confirmation` is the ToolConfirmation the human returned. `decision`
        defaults to approve/reject read from `confirmed`; an override is
        never inferred and must be passed explicitly with the edited args
        (`returned`) or a `diff`.
        """
        approver = approver or self.reviewer
        if approver != self.reviewer:
            self._join(approver)

        args = _args(tool_call)
        artefact = {
            "tool":         _attr(tool_call, "name"),
            "args":         args,
            "tool_call_id": _attr(tool_call, "id"),
        }
        if decision is None:
            decision = "approve" if _confirmed(confirmation) else "reject"

        task_id = self._submit(artefact, approver)

        if decision == "approve":
            self._decide("decide.approve", approver, task_id, rationale, tags)
        elif decision == "override":
            if diff is None:
                if returned is None:
                    raise ValueError(
                        "an override needs `returned` (the edited args) or a `diff`"
                    )
                diff = _diff(args, returned, "/args")
            if not diff:
                # The edit changed nothing; record the approve it really is.
                self._decide("decide.approve", approver, task_id, rationale, tags)
                return task_id
            self._dispatch("decide.override", {
                "workspace": self.workspace,
                "from":      approver,
                "task_id":   task_id,
                "diff":      diff,
                "rationale": rationale or "reviewer edited the tool arguments",
                "tags":      tags or [],
                "intent_preserved": True if intent_preserved is None else intent_preserved,
            })
        elif decision == "reject":
            self._decide("decide.reject", approver, task_id, rationale, tags)
        else:
            raise ValueError(
                f"decision must be 'approve', 'override', or 'reject': {decision!r}"
            )
        return task_id

    def audit(self) -> list[dict]:
        env = self._dispatch("audit.read", {"workspace": self.workspace})
        return env["result"]["entries"]

    # ---- internal -------------------------------------------------

    def _submit(self, artefact: dict, approver: str) -> str:
        """Record the tool call as the agent's task output and send it for
        review -- the agent's half of the handshake."""
        env = self._dispatch("task.create", {
            "workspace": self.workspace,
            "from":      self.agent,
            "assignee":  self.agent,
            "kind":      artefact["tool"] or "tool_call",
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
    "ChapConfirmationBridge",
    "ChapBridgeError",
]
