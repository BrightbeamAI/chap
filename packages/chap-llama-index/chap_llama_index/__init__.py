"""
chap-llama-index: record a CHAP human-decision when a LlamaIndex Workflow
pauses for human input.

A Workflow step returns an InputRequiredEvent carrying the proposed
output and waits; a human replies with a HumanResponseEvent. This adapter
records that reply against the proposed output:

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
never imported here: events are read structurally via event.get(...), so
the same code runs against the real types or a test stand-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from chap_coordinator import Coordinator


__version__ = "0.2.6"


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


def _participant_type(uri: str) -> str:
    scheme = uri.split(":", 1)[0]
    return scheme if scheme in ("human", "agent", "service", "group") else "human"


# ============================================================
#   ChapHitlBridge
# ============================================================


@dataclass
class ChapHitlBridge:
    """One bridge per workspace. Holds the Coordinator, the workspace it
    writes into, the agent whose step proposed the output, and the default
    approver. Share it across runs; pass a per-decision approver when the
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
        """
        approver = approver or _event_get(response_event, "user_name") or self.reviewer
        if approver != self.reviewer:
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

    def _submit(self, artefact: Any, approver: str) -> str:
        """Record the proposed output as the agent's task output and send
        it for review -- the agent's half of the handshake."""
        env = self._dispatch("task.create", {
            "workspace": self.workspace,
            "from":      self.agent,
            "assignee":  self.agent,
            "kind":      "workflow_output",
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
    "ChapHitlBridge",
    "ChapBridgeError",
]
