"""
chap-langgraph: glue between LangGraph workflows and a CHAP Coordinator.

The point of this adapter is to make the human-in-the-loop moment in a
LangGraph workflow audit-visible. LangGraph already supports interrupts
(an interrupt() call inside a node pauses the graph, surfaces state to a
human, and resumes when the human decides). This adapter turns those
events into CHAP envelopes:

    LangGraph event              CHAP envelope
    -----------------            -------------------------
    interrupt() called           task.complete + review.request
    Command(resume=...) sent     decide.approve | decide.reject | decide.override

The pattern lets a single LangGraph deployment produce a hash-linked,
replayable audit chain across every human decision, with no code
changes inside the existing node functions.

Usage:

    from chap_coordinator import Coordinator
    from chap_langgraph import ChapBridge, hil_review

    coord = Coordinator(default_profiles=["core/1.0", "review/1.0"])
    bridge = ChapBridge(
        coord,
        workspace="wsp_my_app",
        agent="agent:drafter#v1",
        reviewer="human:alice@example.org",
    )

    def drafter_node(state):
        draft = produce_draft(state)
        return hil_review(bridge, draft, kind="draft_response")

The hil_review() helper handles the full handshake. For the resume
side, your reducer or post-interrupt node calls
bridge.apply_decision(state, decision_payload) which records the
right envelope and returns updated state.

This is a CHAP-side wrapper, not a fork of LangGraph. It depends on
neither langgraph nor langchain at import time. Pass LangGraph state
dicts in and get LangGraph-shaped values out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from chap_coordinator import Coordinator


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
#   ChapBridge
# ============================================================


@dataclass
class ChapBridge:
    """
    Holds the CHAP Coordinator, the workspace it writes into, and the
    default participants for one LangGraph deployment. One bridge per
    workspace; share it across nodes.
    """

    coord:     Coordinator
    workspace: str
    agent:     str
    reviewer:  str
    profiles:  tuple = ("core/1.0", "review/1.0")
    # Hook for tests / consumers who want to see every envelope before
    # it hits the Coordinator. Receives the params dict.
    on_envelope: Optional[Callable[[str, dict], None]] = None

    def __post_init__(self) -> None:
        # Ensure the workspace exists. workspace.create is idempotent
        # on the same id: the second call is a no-op for the data
        # model and produces a clean error rather than corrupting state.
        _safe_dispatch(self.coord, "workspace.create", {
            "workspace": self.workspace,
            "profiles":  list(self.profiles),
        })
        for uri, type_ in ((self.agent, "agent"), (self.reviewer, "human")):
            _safe_dispatch(self.coord, "participant.join", {
                "workspace": self.workspace,
                "from":      uri,
                "type":      type_,
            })

    # ---- task lifecycle helpers ----------------------------------

    def create_task(self, kind: str, input_: Any) -> str:
        """Open a task that an agent is about to complete."""
        env = self._dispatch("task.create", {
            "workspace": self.workspace,
            "from":      self.agent,
            "assignee":  self.agent,
            "kind":      kind,
            "input":     input_,
        })
        return env["result"]["task_id"]

    def complete_with_review(self, task_id: str, artefact: Any) -> None:
        """
        Record the agent's output and immediately request human review.
        This is the standard pattern for `interrupt()` boundaries:
        the agent has finished its turn and the workflow pauses
        until the human decides.
        """
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
            "to":        self.reviewer,
        })

    # ---- decision helpers ----------------------------------------

    def approve(self, task_id: str, rationale: Optional[str] = None) -> None:
        params: dict = {
            "workspace": self.workspace,
            "from":      self.reviewer,
            "task_id":   task_id,
        }
        if rationale is not None:
            params["rationale"] = rationale
        self._dispatch("decide.approve", params)

    def reject(self, task_id: str, rationale: str,
               category: Optional[str] = None) -> None:
        params: dict = {
            "workspace": self.workspace,
            "from":      self.reviewer,
            "task_id":   task_id,
            "rationale": rationale,
        }
        if category is not None:
            params["category"] = category
        self._dispatch("decide.reject", params)

    def override(self, task_id: str, diff: list[dict[str, Any]],
                 rationale: str,
                 tags: Optional[list[str]] = None,
                 intent_preserved: Optional[bool] = None) -> Any:
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
        env = self._dispatch("decide.override", params)
        return env["result"]["applied"]

    def apply_decision(self, task_id: str,
                       decision: Union[str, dict[str, Any]]) -> Any:
        """
        Translate a LangGraph resume payload into the matching CHAP
        envelope. Accepted shapes (designed to align with what
        LangGraph's Command(resume=...) usually carries):

            "approve"
            "reject"
            {"action": "approve" | "reject" | "override", ...}
            {"diff": [...], "rationale": "..."}  (implied override)

        Returns the artefact value to forward to downstream nodes.
        """
        if isinstance(decision, str):
            action = decision
            payload: dict[str, Any] = {}
        elif isinstance(decision, dict):
            payload = dict(decision)
            action = payload.pop("action", None) or (
                "override" if "diff" in payload else "approve"
            )
        else:
            raise TypeError(f"unexpected decision payload: {type(decision)}")

        if action == "approve":
            self.approve(task_id, rationale=payload.get("rationale"))
            return payload.get("artefact")
        if action == "reject":
            self.reject(
                task_id,
                rationale=payload.get("rationale", "rejected by reviewer"),
                category=payload.get("category"),
            )
            return None
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
            )
        raise ValueError(f"unknown decision action: {action!r}")

    # ---- audit ----------------------------------------------------

    def audit(self) -> list[dict[str, Any]]:
        """Return the full audit chain for this workspace."""
        env = self._dispatch("audit.read", {"workspace": self.workspace})
        return env["result"]["entries"]

    # ---- internal -------------------------------------------------

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
    Wrap a single human-in-the-loop checkpoint in CHAP envelopes.

    Designed to be called inside a LangGraph node that is about to
    interrupt() for human review. Returns a state dict the caller can
    spread into the LangGraph state and immediately interrupt on. The
    returned dict carries:

        chap_task_id   The CHAP task id; pass this back via apply_decision()
                       once the human has decided.
        chap_artefact  The artefact that was sent for review.
        chap_kind      The task kind, for logging/routing.

    The integration with LangGraph looks like:

        def drafter_node(state):
            draft = produce_draft(state)
            chap_state = hil_review(bridge, draft, kind="draft_response")
            decision = interrupt(chap_state)
            applied = bridge.apply_decision(chap_state["chap_task_id"], decision)
            return {"draft": applied or draft, **chap_state}
    """
    task_id = existing_task_id or bridge.create_task(kind, input_ or {})
    bridge.complete_with_review(task_id, artefact)
    return {
        "chap_task_id":  task_id,
        "chap_artefact": artefact,
        "chap_kind":     kind,
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
    "hil_review",
]
