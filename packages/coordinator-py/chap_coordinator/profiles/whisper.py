"""
chap_coordinator.profiles.whisper

The whisper/1.0 profile (profiles/whisper.md).

Deadline-bound interrupt-style question channel. Methods:
  - whisper.ask     -> pose a question with options and default
  - whisper.answer  -> answer a whisper (option-id or free text)

Lapse handling is exposed as ``check_lapses()`` on the coordinator;
the spec requires a notify.message notification when a deadline
passes without an answer. Real deployments wire this to their
scheduler; the reference exposes it as a callable so tests and
short-running demos can advance the timer deterministically.
"""
from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING

from ..jsonrpc import E, rpc_error
from ..types import WhisperPrompt

if TYPE_CHECKING:
    from ..coordinator import Coordinator


def _parse_iso(ts: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def register_whisper(coord: "Coordinator") -> None:
    """Register whisper/1.0 handlers."""

    def whisper_ask(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        for f in ("from", "to", "task_id", "question", "deadline_ms",
                  "default_if_lapsed"):
            if f not in p:
                return {"error": rpc_error(E.PARAMS, f"Missing field: {f}")}
        if p["task_id"] not in ws.tasks:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}

        askee = p["to"] if isinstance(p["to"], list) else [p["to"]]
        prompt_id = p.get("whisper_id") or coord.ids.artefact_id()
        prompt = WhisperPrompt(
            id=prompt_id,
            task_id=p["task_id"],
            asker=p["from"],
            askee=askee,
            question=p["question"],
            options=p.get("options"),
            asked_at=coord.now_iso(),
            deadline_ms=int(p["deadline_ms"]),
            default_if_lapsed=p["default_if_lapsed"],
            urgency=p.get("urgency") or "low",
        )
        ws.whispers[prompt_id] = prompt
        return {"result": {"whisper_id": prompt_id,
                           "deadline_ms": prompt.deadline_ms}}

    def whisper_answer(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        for f in ("from", "whisper_id"):
            if f not in p:
                return {"error": rpc_error(E.PARAMS, f"Missing field: {f}")}
        prompt = ws.whispers.get(p["whisper_id"])
        if not prompt:
            return {"error": rpc_error(E.PARAMS, "Unknown whisper id")}
        # Only a participant the whisper was addressed to may answer it. A
        # broadcast scope (workspace:/group:) is satisfied by any member; the
        # coordinator does not model group membership (see SPECIFICATION.md).
        answerer = p["from"]
        broadcast = any(isinstance(a, str) and (a.startswith("workspace:") or a.startswith("group:"))
                        for a in prompt.askee)
        if broadcast:
            if answerer not in ws.members:
                return {"error": rpc_error(E.NOT_AUTHORISED,
                                           f"Not a workspace member: {answerer}")}
        elif answerer not in prompt.askee:
            return {"error": rpc_error(E.NOT_AUTHORISED,
                                       f"Whisper was not addressed to {answerer}")}
        if prompt.state == "answered":
            return {"error": rpc_error(E.WHISPER_ALREADY_ANSWERED,
                                       "Whisper already answered")}
        if prompt.state == "lapsed":
            return {"error": rpc_error(E.WHISPER_LAPSED,
                                       "Whisper already lapsed")}

        # Per spec, answer carries `answer_option` (the option id) and an
        # optional `comment`. We also accept legacy `answer` (free text)
        # when no options were defined.
        answer_option = p.get("answer_option")
        answer_text = p.get("answer") or p.get("answer_text")
        if prompt.options:
            if answer_option is None:
                return {"error": rpc_error(E.PARAMS,
                                           "answer_option is required when options are defined")}
            valid_ids = {o.get("id") for o in prompt.options if isinstance(o, dict)}
            if answer_option not in valid_ids:
                return {"error": rpc_error(
                    E.WHISPER_OPTION_NOT_IN_SET,
                    f"Answer option {answer_option!r} not in option set")}
        else:
            if not answer_text and answer_option is None:
                return {"error": rpc_error(E.PARAMS,
                                           "answer or answer_option required")}

        prompt.state = "answered"
        prompt.answered_at = coord.now_iso()
        prompt.answered_by = p["from"]
        prompt.answer_option = answer_option
        prompt.answer_text = answer_text
        prompt.comment = p.get("comment")
        return {"result": {"answered": True, "whisper_id": prompt.id}}

    def check_lapses(workspace_id: str, now: str | None = None) -> list[dict]:
        """Apply deadlines to all pending whispers in a workspace.

        For each newly-lapsed whisper, emit a synthetic ``notify.message``
        notification into the audit log (per profiles/whisper.md S4) and
        return the list of notifications emitted. Called by deployments
        on a timer; called by the test suite directly to advance state.
        """
        ws = coord.workspaces.get(workspace_id)
        if not ws:
            return []
        cutoff = _parse_iso(now or coord.now_iso())
        emitted: list[dict] = []
        for prompt in ws.whispers.values():
            if prompt.state != "pending":
                continue
            asked = _parse_iso(prompt.asked_at)
            deadline = asked + _dt.timedelta(milliseconds=prompt.deadline_ms)
            if cutoff < deadline:
                continue
            prompt.state = "lapsed"
            prompt.default_applied = prompt.default_if_lapsed
            notify = {
                "jsonrpc": "2.0",
                "method": "notify.message",
                "params": {
                    "workspace": ws.id,
                    "from": "service:coordinator",
                    "to": [prompt.asker, *prompt.askee],
                    "ts": coord.now_iso(),
                    "kind": "whisper_lapsed",
                    "whisper_id": prompt.id,
                    "default_applied": prompt.default_if_lapsed,
                },
            }
            # Append into the workspace audit log directly
            coord._record_audit(ws, notify)
            emitted.append(notify)
        return emitted

    coord._handlers["whisper.ask"] = whisper_ask
    coord._handlers["whisper.answer"] = whisper_answer
    # Expose lapse-check as a method on the coordinator instance for callers
    # who need to advance the deadline timer (tests, demos, schedulers).
    coord.check_whisper_lapses = check_lapses  # type: ignore[attr-defined]
