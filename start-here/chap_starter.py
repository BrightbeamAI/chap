"""A small, copyable review gate over the real CHAP coordinator.

This is example code, not a published SDK and not a second protocol. It wraps
three Core+review calls so the shape of a reviewed workflow is visible in one
file:

    task.create      an agent has work to show a human
    review.request   the draft becomes the artefact under review
    decide.*         a human approves, edits, or rejects it

The task's ``output`` stays empty until a human decides. Nothing here ever
calls ``task.complete`` on work that is waiting for review, because that
records a completion for output nobody has seen. See README.md, "Why there is
no task.complete here".

One process owns one database. A ``human:`` URI labels a participant; it does
not authenticate a person.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        "CHAP needs Python 3.10 or newer. This is "
        f"{sys.version_info.major}.{sys.version_info.minor}.\n"
        "The coordinator uses X | Y unions at runtime, which 3.9 cannot parse."
    )
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

# Inside the CHAP checkout the coordinator loads from source and nothing needs
# installing. A copied folder uses the installed package instead.
_source = Path(__file__).resolve().parents[1] / "packages" / "coordinator-py"
if (_source / "chap_coordinator").is_dir():
    sys.path.insert(0, str(_source))
try:
    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_coordinator.canonical import content_hash
    from chap_coordinator.storage.sqlite import SqliteStore
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    if exc.name != "chap_coordinator":
        raise
    raise SystemExit(
        "CHAP is missing. Run this inside the CHAP checkout, or install it:\n"
        "  python -m pip install chap-coordinator"
    ) from exc

PROFILES = ["core/1.0", "review/1.0", "audit-scitt/1.0"]


class ReviewPending(RuntimeError):
    """No human decision yet. Do not execute the proposed action."""


class ReviewRejected(RuntimeError):
    """The human rejected this proposal. Do not execute it."""


class ChapError(RuntimeError):
    """The coordinator refused an operation."""


class StorageError(RuntimeError):
    """The decision could not be saved. This gate must be restarted."""


class _CheckedStore:
    """Surface save errors that the reference coordinator otherwise swallows."""

    def __init__(self, path):
        try:
            self.inner = SqliteStore(str(path))
        except sqlite3.DatabaseError as exc:
            raise StorageError(
                f"{path} is not a CHAP database ({exc}). Delete it, or pass "
                "a different --db."
            ) from exc
        self.failure = None

    def load(self):
        try:
            return self.inner.load()
        except Exception as exc:
            self.failure = exc
            raise

    def save(self, record):
        try:
            return self.inner.save(record)
        except Exception as exc:
            self.failure = exc
            raise

    def delete(self, workspace):
        return self.inner.delete(workspace)

    def close(self):
        self.inner.close()


class _DatabaseLock:
    """An OS lock, released on crash, so two writers cannot lose entries."""

    def __init__(self, path):
        self.file = open(str(path) + ".lock", "a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise StorageError("This database is already open. Stop its other demo first.") from exc

    def close(self):
        self.file.close()


def json_diff(before, after, path=""):
    """An RFC 6902 patch. Recurse through objects; replace changed arrays whole."""
    # Python treats True == 1, including inside dicts and lists. JSON does not,
    # so compare canonical hashes rather than Python equality.
    if content_hash(before) == content_hash(after):
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        patch = []
        escape = lambda key: key.replace("~", "~0").replace("/", "~1")
        for key in sorted(before.keys() - after.keys()):
            patch.append({"op": "remove", "path": path + "/" + escape(key)})
        for key in sorted(after):
            target = path + "/" + escape(key)
            if key not in before:
                patch.append({"op": "add", "path": target, "value": after[key]})
            else:
                patch.extend(json_diff(before[key], after[key], target))
        return patch
    return [{"op": "replace", "path": path, "value": after}]


class ReviewGate:
    """propose, then a human decides, then result. Every step is a real CHAP event.

    Call ``decide`` only from a trusted human input boundary. The browser
    example keeps separate agent and reviewer capabilities so the difference is
    visible locally; that is a teaching device, not an authentication system.
    """

    def __init__(self, *, db=None, workspace="wsp_starter",
                 agents=("agent:demo",), reviewer="human:you@local"):
        agents = tuple(agents)
        if not agents:
            raise ValueError("Register at least one agent URI")
        for uri in agents:
            if not isinstance(uri, str) or not uri.startswith("agent:"):
                raise ValueError("every agent must be an agent: URI")
        if not isinstance(reviewer, str) or not reviewer.startswith("human:"):
            raise ValueError("reviewer must be a human: URI")
        self.workspace, self.agents, self.reviewer = workspace, agents, reviewer
        self._lock = self._store = None
        try:
            if db is not None:
                path = Path(db).resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                self._lock = _DatabaseLock(path)
                self._store = _CheckedStore(path)
            self.coordinator = Coordinator(CoordinatorOptions(
                default_profiles=PROFILES, store=self._store,
            ))
            self._healthy()
            members = [(reviewer, "human")] + [(uri, "agent") for uri in agents]
            ws = self.coordinator.get_workspace(workspace)
            if ws is None:
                self._send("workspace.create", actor=agents[0], profiles=PROFILES)
                for uri, kind in members:
                    self._send("participant.join", actor=uri, type=kind)
            else:
                if not all(profile in ws.profiles for profile in PROFILES):
                    raise StorageError(
                        f"The workspace in this database predates chaining. Delete "
                        f"{db} and start again, or pass a different --db.")
                for uri, kind in members:
                    member = ws.members.get(uri)
                    if member is None or member.type != kind:
                        raise StorageError(
                            f"The workspace in this database has different participants. "
                            f"Delete {db} and start again, or pass a different --db.")
            self.verify()
        except BaseException:
            self.close()
            raise

    # -- internals ---------------------------------------------------------

    def _healthy(self):
        if self._store is not None and self._store.failure is not None:
            raise StorageError("CHAP could not save this session. Stop, check the database, and restart.")

    def _send(self, method, *, actor=None, **params):
        self._healthy()
        result = self.coordinator.dispatch({
            "jsonrpc": "2.0", "id": uuid4().hex, "method": method,
            "params": {"workspace": self.workspace,
                       "from": actor or self.agents[0], **copy.deepcopy(params)},
        })
        self._healthy()
        if "error" in result:
            error = result["error"]
            raise ChapError(f"{method}: {error['message']} ({error['code']})")
        return copy.deepcopy(result["result"])

    def _task(self, task_id):
        self._healthy()
        workspace = self.coordinator.get_workspace(self.workspace)
        if task_id not in workspace.tasks:
            raise KeyError("Unknown task")
        return workspace.tasks[task_id]

    # -- the three steps ---------------------------------------------------

    def propose(self, draft: dict, *, kind="draft_response", context=None, agent=None):
        """Record a JSON object from your model or tool and ask a human to review it.

        Two envelopes: ``task.create`` opens the work, ``review.request`` puts
        the draft in front of the reviewer. The task's ``output`` is still
        empty when this returns, and stays empty until someone decides.
        """
        if not isinstance(draft, dict):
            raise ValueError("draft must be a JSON object; wrap text as {'text': text}")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("kind must be a non-empty string")
        if len(kind) > 120:
            raise ValueError("kind must be 120 characters or fewer")
        if context is not None and not isinstance(context, dict):
            raise ValueError("context must be a JSON object")
        author = agent or self.agents[0]
        if author not in self.agents:
            raise ValueError(f"{author} is not a registered agent for this workspace")
        # Validate the whole payload before opening a task, so a bad draft
        # never leaves a half-built task in the log. CHAP carries safe integer
        # numbers; write decimals as strings, for example {"amount": "12.50"}.
        content_hash({"draft": draft, "context": context, "kind": kind})
        task_id = self._send("task.create", actor=author, kind=kind,
                             input=context or {}, assignee=author)["task_id"]
        self._send("review.request", actor=author, task_id=task_id,
                   artefact=draft, to=self.reviewer)
        return task_id

    def decide(self, task_id, action, *, edited=None, rationale="", tags=None,
               expected_digest=None, intent_preserved=None):
        """Record one human decision. Blank or unknown actions are refused."""
        if action not in ("approve", "edit", "reject"):
            raise ValueError("Choose approve, edit, or reject explicitly")
        if not isinstance(rationale, str):
            raise ValueError("rationale must be text")
        if action in ("edit", "reject") and not rationale.strip():
            raise ValueError("Explain why you changed or rejected this draft")
        if tags is not None and (not isinstance(tags, list) or
                                 any(not isinstance(tag, str) for tag in tags)):
            raise ValueError("tags must be a list of strings")
        if intent_preserved is not None and type(intent_preserved) is not bool:
            raise ValueError("intent_preserved must be true, false, or omitted")
        view = self.inspect(task_id)
        if view["state"] != "review_requested":
            raise ValueError("This review has already been decided or is not open")
        if expected_digest is not None and expected_digest != view["digest"]:
            raise ValueError("The draft changed. Reload it before deciding")
        params = {"task_id": task_id, "rationale": rationale, "comment": rationale,
                  "tags": tags or [], "approved_artefact_digest": view["digest"]}
        if action == "edit":
            if not isinstance(edited, dict):
                raise ValueError("The edited draft must be a JSON object")
            content_hash(edited)
            params["diff"] = json_diff(view["draft"], edited)
            if not params["diff"]:
                raise ValueError("Nothing changed. Use approve instead")
            if intent_preserved is not None:
                params["intent_preserved"] = intent_preserved
        elif edited is not None:
            raise ValueError("Use action='edit' to approve changed content")
        method = {"approve": "decide.approve", "edit": "decide.override",
                  "reject": "decide.reject"}[action]
        self._send(method, actor=self.reviewer, **params)
        return self.inspect(task_id)

    def result(self, task_id):
        """Return the exact reviewed object, or raise. Never falls back to the draft."""
        self.verify()
        view = self.inspect(task_id)
        if view["state"] == "declined":
            raise ReviewRejected("The human rejected this proposal")
        if not view["allowed"]:
            raise ReviewPending("Waiting for an explicit human decision")
        return view["output"]

    # -- reading -----------------------------------------------------------

    def inspect(self, task_id):
        """A detached view. A UI can edit it freely without touching CHAP state."""
        task = self._task(task_id)
        decisions = task.review.decisions if task.review else []
        decision = decisions[-1] if decisions else None
        permitted = bool(task.state == "completed" and decision
                         and decision["kind"] in ("approve", "override")
                         and decision["reviewer"] == self.reviewer)
        return copy.deepcopy({
            "task_id": task_id, "kind": task.kind, "state": task.state,
            "author": task.assignee, "context": task.input,
            "draft": task.pending_artefact,
            "digest": content_hash(task.pending_artefact),
            "decision": decision, "allowed": permitted,
            "output": task.output if permitted else None,
        })

    def tasks(self):
        workspace = self.coordinator.get_workspace(self.workspace)
        return [self.inspect(task_id) for task_id in workspace.tasks]

    def audit(self):
        return self._send("audit.read")["entries"]

    def verify(self):
        """Replay the chain. Raises unless the whole log verifies."""
        verdict = self._send("audit.verify_chain")
        if verdict.get("ok") is not True or verdict.get("status") != "verified":
            raise ChapError("The local audit chain did not verify: "
                            + str(verdict.get("status", "unknown")))
        return verdict

    def verdict(self):
        """The chain verdict as data, for a UI that must show a failure rather than raise.

        Always returns a dict with ``ok`` and ``status``. A caller that renders
        this must treat anything other than ``status == "verified"`` as a
        failure; there is no state in which the absence of a verdict means the
        chain is fine.
        """
        try:
            return self._send("audit.verify_chain")
        except (ChapError, StorageError) as exc:
            return {"ok": False, "status": "unavailable", "detail": str(exc)}

    def export(self, path):
        """Portable evidence: the real envelopes and this machine's chain verdict."""
        body = {"format": "chap-starter-evidence/1", "workspace": self.workspace,
                "verification": self.verdict(), "entries": self.audit(),
                "snapshot": asdict(self.coordinator.get_workspace(self.workspace))}
        Path(path).write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        return Path(path)

    # -- lifecycle ---------------------------------------------------------

    def close(self):
        if self._store is not None:
            self._store.close()
            self._store = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def ask_in_terminal(gate, task_id, *, read=input, write=print):
    """One explicit decision. EOF, Ctrl-C, or a blank line leaves it pending."""
    view = gate.inspect(task_id)
    write(json.dumps(view["draft"], indent=2, ensure_ascii=False))
    try:
        action = read("[a]pprove / [e]dit / [r]eject / Enter to leave pending: ").strip().lower()
        if action not in ("a", "e", "r"):
            write("Left pending. Nothing is authorised to run.")
            return None
        edited = None
        if action == "e":
            edited = json.loads(read("Paste the edited JSON object on one line: "))
        reason = read("Reason: ") if action in ("e", "r") else ""
        return gate.decide(task_id, {"a": "approve", "e": "edit", "r": "reject"}[action],
                           edited=edited, rationale=reason, expected_digest=view["digest"])
    except (EOFError, KeyboardInterrupt):
        write("\nLeft pending. Nothing is authorised to run.")
        return None
