"""
chap-pydantic-ai: record a CHAP human-decision when a Pydantic AI run
pauses for tool approval.

Pydantic AI gates a tool with requires_approval=True (or an
ApprovalRequiredToolset). The run then ends with a DeferredToolRequests
output whose .approvals holds the pending ToolCallParts. The caller
resolves each one into DeferredToolResults.approvals and feeds it back
via deferred_tool_results=. This adapter records the decision at that
resolution point:

    Pydantic AI resolution            CHAP envelope
    ------------------------          ---------------------------
    True / ToolApproved()             decide.approve
    ToolApproved(override_args=...)   decide.override  (diff of the args)
    False / ToolDenied(message=...)   decide.reject

The proposed tool call (tool name, args, tool_call_id) is the artefact
under review; the resolution is the human's decision on it. Every
envelope lands in the workspace audit chain, hash-linked and replayable.

The bridge depends only on chap-coordinator. Pydantic AI is never
imported here: the resolution objects are read structurally, so the same
code runs against the real types or a test stand-in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from chap_coordinator import Coordinator


try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    __version__ = _pkg_version("chap-pydantic-ai")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+source"


def _make_envelope(method: str, params: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id":      f"chap-pydantic-ai-{method}",
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


def _participant_type(uri: str) -> str:
    """CHAP participant type from the URI scheme: human:alice -> human,
    agent:bot -> agent. The type then matches the identity instead of
    being assumed."""
    scheme = uri.split(":", 1)[0]
    return scheme if scheme in ("human", "agent", "service", "group") else "human"


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
#   ChapApprovalBridge
# ============================================================


@dataclass
class ChapApprovalBridge:
    """One bridge per workspace. Holds the Coordinator, the workspace it
    writes into, the agent that proposed the tool calls, and the default
    approver. Share it across runs; pass a per-decision approver when the
    decision belongs to someone else.
    """

    coord:     Coordinator
    workspace: str
    agent:     str
    reviewer:  str
    profiles:  tuple = ("core/1.0", "review/1.0")
    # Lets tests / consumers observe every envelope before it reaches the
    # Coordinator. Receives (method, params).
    on_envelope: Optional[Callable[[str, dict], None]] = None

    def __post_init__(self) -> None:
        _safe_dispatch(self.coord, "workspace.create", {
            "workspace": self.workspace,
            "profiles":  list(self.profiles),
        })
        self._join(self.agent)
        self._join(self.reviewer)

    def record_decision(self, call: Any, resolution: Any, *,
                        approver: Optional[str] = None,
                        rationale: Optional[str] = None,
                        tags: Optional[list[str]] = None,
                        intent_preserved: Optional[bool] = None) -> str:
        """Record one resolved approval and return the task id.

        `call` is the pending ToolCallPart; `resolution` is what the
        caller put in DeferredToolResults.approvals (bool, ToolApproved,
        or ToolDenied). The keyword arguments are the human's signal that
        the resolution object can't carry on its own -- a resolver lifts
        them from DeferredToolResults.metadata[tool_call_id].
        """
        approver = approver or self.reviewer
        if approver != self.reviewer:
            self._join(approver)

        args = _arguments(call)
        artefact = {
            "tool":         getattr(call, "tool_name", None),
            "args":         args,
            "tool_call_id": getattr(call, "tool_call_id", None),
        }
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

    def _submit(self, artefact: dict, approver: str) -> str:
        """Open the task, record the agent's tool call as its output, and
        send it for review -- the agent's half of the handshake."""
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
    "ChapApprovalBridge",
    "ChapBridgeError",
]
