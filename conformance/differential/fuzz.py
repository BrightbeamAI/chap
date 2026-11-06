"""
Differential conformance fuzzer for the two reference coordinators.

CHAP's headline guarantee is that two independently-authored coordinators
produce byte-identical results (SPECIFICATION.md). Static vectors check that on
hand-written cases; this checks it on random ones.

For each seed it drives the Python reference with a random sequence of calls,
records the concrete envelopes it sent (ids are server-minted, so a sequence
cannot be written in advance), replays that exact sequence into the
TypeScript reference in a subprocess, and asserts the two agree on every
response, on the state each holds after every call, and on the audit-chain
head. A subprocess boundary is used so no serialisation layer masks or
invents a difference. An answer of -32603 from either fails the seed: it is a
fault in the coordinator, whatever the other answered.

The action set covers the core and review lifecycle, control, handoff, whisper,
deliberation and routing, including canonical control.snapshot artefacts and
subsequent rollback, and participant pauses, which the calls that assign work
then meet.

Each envelope is recorded as the client sent it, taken before dispatch, and
every seed also asserts that dispatch left it untouched. A coordinator records
what it received: a handler that writes into the request makes the recorded
envelope differ from the signed one, and would otherwise be replayed into the
other reference as though both had produced it.

Each seed is also replayed into both references on a SQLite file. Each call
writes only what it changed, so the check is that nothing was missed: a
coordinator restarted on the file holds what the one that wrote it held, and
the two references wrote the same audit entries.

Action selection reads the workspace as it stands: decisions go to tasks under
review and to reviewers the review named, resumes go to paused tasks, handoffs
are resolved by the participant they were offered to, votes come from
participants who have not voted. A share of the steps is a deliberate miss, so
the refusals are compared as well as the successes. Overrides draw from the
whole JSON Patch operation set against documents with arrays and nesting,
including the `1e1` and `1_0` index tokens behind #103.

Another share is malformed: a call to any method with one or two of the params
it reads missing, null or of another type, the workspace and the sender
included, params that are not an object, and envelopes that are not a call at
all. The state comparison is what makes these worth sending: a handler that
changed state and then refused or raised shows up there even when both
references answered alike.

Usage:
    python fuzz.py --seeds 200          # sweep seeds 0..199
    python fuzz.py --seed 42            # one seed, verbose on divergence
    python fuzz.py --seeds 50 --steps 60
    python fuzz.py --seeds 40 --stats   # with per-method coverage
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.sqlite import SqliteStore

_ROOT = Path(__file__).resolve().parents[2]
_REPLAY = _ROOT / "conformance" / "differential" / "replay.ts"

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "handoff/1.0",
            "whisper/1.0", "deliberation/1.0", "routing/1.0"]
HUMANS = ["human:a", "human:c"]
AGENTS = ["agent:b", "agent:d"]


# Every method a reference dispatches, with the params its handler reads. The
# malformed calls draw on these, so each method meets a missing or ill-typed
# value in every param it reads, and its refusal, its state and its log are
# compared like any other call's.
METHOD_PARAMS: dict[str, tuple[str, ...]] = {
    "workspace.create": ("profiles", "mode", "mode_ceiling", "routing_policy_uri",
                         "step_up_window_sec", "min_acr"),
    "workspace.describe": (),
    "workspace.set_profiles": ("profiles",),
    "participant.join": ("type", "role", "display_name", "capabilities", "scopes", "jwks",
                         "oidc_token", "vc_presentation"),
    "participant.leave": (),
    "participant.rotate_key": ("old_kid", "new_jwk"),
    "participant.revoke_key": ("target_uri", "kid", "reason"),
    "task.create": ("kind", "input", "assignee", "to", "mode", "review_required", "deadline",
                    "routing_hints", "idempotency_key"),
    "task.update": ("task_id", "state", "progress_note"),
    "task.complete": ("task_id", "output", "confidence"),
    "audit.read": ("range", "filter"),
    "review.request": ("task_id", "artefact", "to", "rule", "deadline"),
    "decide.approve": ("task_id", "comment", "tags", "approved_artefact_digest"),
    "decide.reject": ("task_id", "comment", "tags", "request_revision",
                      "approved_artefact_digest"),
    "decide.override": ("task_id", "diff", "rationale", "tags", "policy_refs", "logical_id",
                        "instance_id", "intent_preserved", "approved_artefact_digest"),
    "abstain.declare": ("task_id", "reason", "category"),
    "escalate.raise": ("original_task_id", "new_task"),
    "whisper.ask": ("task_id", "to", "question", "options", "deadline_ms",
                    "default_if_lapsed", "urgency", "whisper_id"),
    "whisper.answer": ("whisper_id", "answer", "answer_text", "answer_option", "comment"),
    "deliberate.open": ("to", "participants", "rule", "question", "weights", "veto",
                        "deadline", "task_id", "deliberation_id"),
    "deliberate.comment": ("deliberation_id", "comment", "text"),
    "deliberate.vote": ("deliberation_id", "vote", "weight", "comment", "veto_invoked"),
    "deliberate.close": ("deliberation_id",),
    "handoff.propose": ("to", "tasks", "summary", "context_links", "handoff_id"),
    "handoff.accept": ("handoff_id", "accepted_task_ids", "comment"),
    "handoff.decline": ("handoff_id", "reason", "suggested_target"),
    "control.pause": ("scope", "task_id", "participant_uri", "uri", "in_flight_policy",
                      "reason"),
    "control.resume": ("scope", "task_id", "participant_uri", "uri"),
    "control.cancel": ("task_id", "reason"),
    "control.snapshot": ("include", "label"),
    "control.rollback": ("to_snapshot_artefact_id", "snapshot_artefact_id", "snapshot_id",
                         "what_to_restore", "reason"),
    "control.supersede": ("task_id", "successor_task", "reason"),
    "control.set_mode_ceiling": ("new_ceiling", "mode_ceiling", "reason"),
    "task.route": ("task_id", "candidates"),
    "review.depth": ("task_id", "artefact_routing_hints"),
    "escalate.auto": ("task_id", "default_escalation_target"),
    "audit.verify_chain": ("from_seq", "to_seq"),
    "audit.verify_receipt": ("receipt",),
    "audit.submit_to_scitt": ("range", "issuer"),
}

# Values a param of the wrong type or shape takes. 2**53 lies past the
# canonical number range, so it is refused at ingress.
JUNK = (None, 0, 7, -1, True, False, "", "x", "human:a", [], ["x"], [1, 2], {},
        {"a": 1}, [{}], [None], {"kind": 5, "assignee": 3}, "__proto__",
        "constructor", 2 ** 53)


class Recorder:
    """Drive the Python reference and record the envelopes it accepts."""

    def __init__(self, seed: int, steps: int):
        self.rnd = random.Random(seed)
        self.coord = Coordinator(CoordinatorOptions(
            deterministic_ids=True, deterministic_clock=True,
            enable_chain=True, default_profiles=PROFILES))
        self.envelopes: list[dict] = []
        self.dispatched: list[dict] = []
        self.responses: list[dict] = []
        self.states: list[dict] = []
        self.tasks: list[str] = []
        self.handoffs: list[str] = []
        self.snapshots: list[str] = []
        self.whispers: list[str] = []
        self.delibs: list[str] = []
        # The artefact last put under review for a task, so an override can
        # patch the document that is actually there.
        self.artefacts: dict[str, dict] = {}
        self.sent: Counter[str] = Counter()
        self.ok: Counter[str] = Counter()
        self._n = 0
        self._bootstrap()
        for _ in range(steps):
            self._step()

    def _send(self, method: str, actor: object, **params) -> dict:
        self._n += 1
        env = {"jsonrpc": "2.0", "id": f"e{self._n}", "method": method,
               "params": {"workspace": "w", "from": actor, **params}}
        # Record what the client sent, not what dispatch left behind. A handler
        # that writes into params would otherwise be replayed into the other
        # reference, and the two would agree on a value only one of them had
        # produced. Keeping the sent copy also lets the recorded envelope be
        # compared with the dispatched one, which is the invariant below.
        sent = json.loads(json.dumps(env))
        resp = self.coord.dispatch(env)
        self.dispatched.append(env)
        self.envelopes.append(sent)
        self.responses.append(resp)
        self.states.append(project(self.coord))
        self.sent[method] += 1
        if "result" in resp:
            self.ok[method] += 1
        return resp

    def _send_params(self, method: str, params: object) -> dict:
        """Send params exactly as given, workspace and from included or not."""
        self._n += 1
        env = {"jsonrpc": "2.0", "id": f"e{self._n}", "method": method}
        if params is not ...:
            env["params"] = params
        return self._send_envelope(method, env)

    def _send_envelope(self, method: str, env: object) -> dict:
        """Send an envelope exactly as given, whether or not it is one."""
        sent = json.loads(json.dumps(env))
        resp = self.coord.dispatch(env)
        self.dispatched.append(env)
        self.envelopes.append(sent)
        self.responses.append(resp)
        self.states.append(project(self.coord))
        self.sent[method] += 1
        if "result" in resp:
            self.ok[method] += 1
        return resp

    def recording_matches_what_was_sent(self) -> list[str]:
        """Methods whose envelope the coordinator altered during dispatch.

        A coordinator records what it received. Anything here is a handler
        writing into the request, which makes the recorded envelope differ
        from the signed one and makes this fuzzer replay one reference's
        mutation into the other.
        """
        return [sent["method"]
                for sent, after in zip(self.envelopes, self.dispatched)
                if sent != after]

    def _bootstrap(self) -> None:
        self._send("workspace.create", "human:a", profiles=PROFILES)
        self._send("participant.join", "human:a", type="human")
        self._send("participant.join", "human:c", type="human")
        for a in AGENTS:
            self._send("participant.join", a, type="agent")

    def _task(self) -> str | None:
        return self.rnd.choice(self.tasks) if self.tasks else None

    def _result(self, r: dict, key: str) -> str | None:
        return r.get("result", {}).get(key) if "result" in r else None

    # -- what the workspace can legally be asked to do right now -----------

    def _by_state(self, *states: str) -> list[str]:
        tasks = self.coord.get_workspace("w").tasks
        return [t for t in self.tasks if t in tasks and tasks[t].state in states]

    def _open_handoffs(self) -> list[str]:
        hs = self.coord.get_workspace("w").handoffs
        return [h for h in self.handoffs if h in hs and hs[h].state == "proposed"]

    def _unanswered_whispers(self) -> list[str]:
        ws = self.coord.get_workspace("w").whispers
        return [w for w in self.whispers
                if w in ws and ws[w].state == "pending"]

    def _open_delibs(self) -> list[str]:
        ds = self.coord.get_workspace("w").deliberations
        return [d for d in self.delibs
                if d in ds and ds[d].state == "open" and self._can_vote(d)]

    def _can_vote(self, did: str) -> list[str]:
        """Participants of this deliberation who have not voted yet."""
        d = self.coord.get_workspace("w").deliberations[did]
        voted = {v.get("voter") for v in (d.votes or [])}
        return [p for p in (d.participants or []) if p not in voted and p in HUMANS]

    def _paused_participants(self) -> list[str]:
        members = self.coord.get_workspace("w").members
        return [u for u in (*HUMANS, *AGENTS) if u in members and members[u].paused]

    def _reviewers(self, tid: str) -> list[str]:
        """Who the open review on this task is addressed to."""
        task = self.coord.get_workspace("w").tasks.get(tid)
        if task is None or task.review is None:
            return list(HUMANS)
        named = [r for r in (task.review.requested_to or []) if r in HUMANS]
        return named or list(HUMANS)

    def _menu(self) -> list[str]:
        """Actions weighted towards what the current state admits.

        A generator that picks a random task for every decision spends its
        budget comparing error responses. Weighting towards legal transitions
        puts the review path, which is the profile CHAP is judged on, in the
        middle of the run rather than at its edges. A fixed share of misses
        keeps the refusals covered.
        """
        live = self._by_state("created", "in_progress")
        menu = ["create", "create", "update", "complete", "complete",
                "whisper", "deliberate", "snapshot", "miss", "pause_participant",
                "ceiling", "leave_and_rejoin", "malformed", "malformed", "malformed"]
        if self._paused_participants():
            menu += ["resume_participant"] * 2
        if live:
            menu += ["review"] * 4
        if self._by_state("review_requested"):
            menu += ["decide"] * 8
        if self._by_state("paused"):
            menu += ["resume"] * 3
        if live:
            menu += ["pause", "handoff", "route", "depth", "auto", "escalate",
                     "supersede", "cancel"]
        if self._open_handoffs():
            menu += ["resolve_handoff"] * 3
        if self._unanswered_whispers():
            menu += ["answer_whisper"] * 2
        if self._open_delibs():
            menu += ["vote"] * 2
        if self.snapshots:
            menu += ["rollback"] * 2
        return menu

    # -- the documents under review, and the patches applied to them -------

    def _artefact(self) -> dict:
        """A document with arrays and nesting, so a patch has somewhere to go."""
        rnd = self.rnd
        return {
            "text": rnd.choice(["draft", "second pass"]),
            "items": [{"id": i, "note": f"n{i}"} for i in range(rnd.randint(1, 3))],
            "meta": {"tags": rnd.sample(["tone", "fact", "legal"], rnd.randint(1, 3)),
                     "score": rnd.randint(0, 9)},
        }

    def _patch(self, doc: dict) -> list[dict]:
        """A JSON Patch against `doc`, drawn from the whole operation set.

        The index tokens matter: `1e1` and `1_0` are the tokens behind #103,
        where one reference parsed them as ten and the other refused. They are
        generated deliberately so a divergence of that class shows up here.
        """
        rnd = self.rnd
        last = len(doc["items"]) - 1
        choices: list[list[dict]] = [
            [{"op": "replace", "path": "/text", "value": "corrected"}],
            [{"op": "add", "path": "/items/-", "value": {"id": 9, "note": "added"}}],
            [{"op": "remove", "path": f"/items/{last}"}],
            [{"op": "replace", "path": f"/items/{rnd.randint(0, last)}/note", "value": "fixed"}],
            [{"op": "move", "from": "/meta/score", "path": "/score"}],
            [{"op": "copy", "from": "/text", "path": "/meta/original"}],
            [{"op": "test", "path": "/text", "value": doc["text"]},
             {"op": "replace", "path": "/meta/score", "value": 5}],
            [{"op": "add", "path": "/meta/tags/0", "value": "urgent"},
             {"op": "remove", "path": "/meta/tags/-"}],
            # Index tokens that are not plain digits. Both references must
            # refuse them, and must refuse them the same way.
            [{"op": "replace", "path": "/items/1e1/note", "value": "ten"}],
            [{"op": "replace", "path": "/items/1_0/note", "value": "ten"}],
            [{"op": "add", "path": "/items/01", "value": {"id": 1}}],
        ]
        return rnd.choice(choices)

    def _step(self) -> None:  # noqa: C901 - a menu of actions
        rnd = self.rnd
        a = rnd.choice(self._menu())
        if a == "create" or not self.tasks:
            self._create()
            return
        human = rnd.choice(HUMANS)

        if a == "miss":
            # A call aimed at a task that is in the wrong state for it, so the
            # refusals stay compared as well as the successes.
            self._miss()
            return
        if a == "malformed":
            self._malformed()
            return
        if a == "decide":
            tid = rnd.choice(self._by_state("review_requested"))
            human = rnd.choice(self._reviewers(tid))
            kind = rnd.choice(["decide.approve", "decide.reject",
                               "decide.override", "decide.override",
                               "abstain.declare"])
            if kind == "abstain.declare":
                self._send(kind, human, task_id=tid, reason="conflict of interest")
            elif kind == "decide.override":
                doc = self.artefacts.get(tid) or {"text": "draft", "items": [{"id": 0}],
                                                  "meta": {"tags": [], "score": 0}}
                self._send(kind, human, task_id=tid, rationale="r",
                           diff=self._patch(doc),
                           tags=rnd.sample(["tone", "fact"], rnd.randint(0, 2)))
            else:
                p: dict = {"task_id": tid, "comment": "c"}
                if kind == "decide.reject":
                    p["request_revision"] = rnd.random() < 0.5
                self._send(kind, human, **p)
            return
        if a == "resume":
            self._send("control.resume", "human:a",
                       task_id=rnd.choice(self._by_state("paused")))
            return
        if a == "pause_participant":
            # A paused participant is given no new work, so task.create,
            # task.route, escalate.raise, control.supersede and handoff.accept
            # meet the pause on both sides of the comparison.
            self._send("control.pause", "human:a", scope="participant",
                       participant_uri=rnd.choice([*HUMANS, *AGENTS]))
            return
        if a == "resume_participant":
            self._send("control.resume", "human:a", scope="participant",
                       participant_uri=rnd.choice(self._paused_participants()))
            return
        if a == "ceiling":
            # Never below trial, the workspace's default mode, so task.create
            # keeps working while a production successor meets the ceiling.
            self._send("control.set_mode_ceiling", "human:a", reason="adjust",
                       new_ceiling=rnd.choice(["trial", "production", "production"]))
            return
        if a == "leave_and_rejoin":
            # A member leaves and joins again at once, which also lifts a pause.
            who = rnd.choice([*HUMANS, *AGENTS])
            self._send("participant.leave", who)
            self._send("participant.join", who,
                       type="human" if who.startswith("human:") else "agent")
            return
        if a == "resolve_handoff":
            hid = rnd.choice(self._open_handoffs())
            who = self.coord.get_workspace("w").handoffs[hid].recipient
            roll = rnd.random()
            if roll < 0.2:
                # Refused since #151: an explicit empty acceptance is an audit
                # record of a transfer that did not happen.
                self._send("handoff.accept", who, handoff_id=hid, accepted_task_ids=[])
            elif roll < 0.7:
                self._send("handoff.accept", who, handoff_id=hid)
            else:
                self._send("handoff.decline", who, handoff_id=hid, reason="not mine")
            return
        if a == "answer_whisper":
            wid = rnd.choice(self._unanswered_whispers())
            asked = self.coord.get_workspace("w").whispers[wid].askee or list(HUMANS)
            self._send("whisper.answer", rnd.choice(asked), whisper_id=wid,
                       answer=rnd.choice(["yes", "no"]))
            return
        if a == "vote":
            did = rnd.choice(self._open_delibs())
            self._send("deliberate.vote", rnd.choice(self._can_vote(did)),
                       deliberation_id=did, vote=rnd.choice(["yea", "nay", "abstain"]))
            if rnd.random() < 0.4:
                self._send("deliberate.close", human, deliberation_id=did)
            return
        if a == "rollback":
            roll = rnd.random()
            if roll < 0.2:
                params = {"what_to_restore": []}      # refused since #152
            elif roll < 0.6:
                params = {}
            else:
                params = {"what_to_restore":
                          rnd.sample(["members", "mode_ceiling"], rnd.randint(1, 2))}
            self._send("control.rollback", human,
                       to_snapshot_artefact_id=rnd.choice(self.snapshots), **params)
            return
        if a == "snapshot":
            slices = ["members", "open_tasks", "mode_ceiling", "policy", "audit"]
            roll = rnd.random()
            if roll < 0.15:
                params = {"include": []}              # refused since #152
            elif roll < 0.45:
                params = {}
            else:
                params = {"include": rnd.sample(slices, rnd.randint(1, len(slices)))}
            snapshot_id = self._result(self._send("control.snapshot", human, **params),
                                       "snapshot_artefact_id")
            if snapshot_id:
                self.snapshots.append(snapshot_id)
            return

        live = self._by_state("created", "in_progress")
        if not live:
            self._create()
            return
        tid = rnd.choice(live)
        if a == "update":
            self._send("task.update", self._assignee(tid), task_id=tid,
                       state=rnd.choice(["in_progress", "in_progress", "declined"]))
        elif a == "review":
            doc = self._artefact()
            self.artefacts[tid] = doc
            r = self._send("review.request", self._assignee(tid), task_id=tid,
                           artefact=doc, to=rnd.sample(HUMANS, rnd.randint(1, 2)),
                           rule=rnd.choice(["any_one_approves", "all_approve", "quorum:2"]))
            if "error" in r:
                self.artefacts.pop(tid, None)
        elif a == "complete":
            doc = self._artefact()
            self.artefacts[tid] = doc
            self._send("task.complete", self._assignee(tid), task_id=tid, output=doc)
        elif a == "pause":
            self._send("control.pause", "human:a", task_id=tid, reason="hold")
        elif a == "escalate":
            new_task = {"kind": "k", "input": {}, "assignee": AGENTS[0]}
            if rnd.random() < 0.3:
                new_task["mode"] = rnd.choice(["shadow", "trial", "production"])
            r = self._send("escalate.raise", "human:a", original_task_id=tid, reason="up",
                           new_task=new_task)
            nt = self._result(r, "new_task_id")
            if nt:
                self.tasks.remove(tid)
                self.tasks.append(nt)
        elif a == "supersede":
            successor = {"kind": "v2", "input": {}, "assignee": AGENTS[0]}
            if rnd.random() < 0.3:
                successor["review_required"] = rnd.choice([True, False])
            r = self._send("control.supersede", "human:a", task_id=tid, reason="redo",
                           successor_task=successor)
            nt = self._result(r, "new_task_id")
            if nt:
                self.tasks.remove(tid)
                self.tasks.append(nt)
        elif a == "cancel":
            if "result" in self._send("control.cancel", "human:a", task_id=tid, reason="no"):
                self.tasks.remove(tid)
        elif a == "handoff":
            r = self._send("handoff.propose", self._assignee(tid),
                           to=rnd.choice(HUMANS), tasks=[{"task_id": tid}])
            hid = self._result(r, "handoff_id")
            if hid:
                self.handoffs.append(hid)
        elif a == "whisper":
            r = self._send("whisper.ask", self._assignee(tid), task_id=tid,
                           to=[rnd.choice(HUMANS)], question=f"q{self._n}",
                           deadline_ms=rnd.choice([0, 600_000]), default_if_lapsed="no")
            wid = self._result(r, "whisper_id")
            if wid:
                self.whispers.append(wid)
        elif a == "deliberate":
            r = self._send("deliberate.open", human, to=rnd.sample(HUMANS, rnd.randint(1, 2)),
                           rule=rnd.choice(["any_one_approves", "quorum:2"]),
                           question=f"Q{self._n}", task_id=tid)
            did = self._result(r, "deliberation_id")
            if did:
                self.delibs.append(did)
        elif a == "route":
            self._send("task.route", "human:a", task_id=tid, candidates=list(AGENTS))
        elif a == "depth":
            self._send("review.depth", "human:a", task_id=tid,
                       artefact_routing_hints={"criticality": "high"})
        elif a == "auto":
            self._send("escalate.auto", "human:a", task_id=tid,
                       default_escalation_target=rnd.choice(HUMANS))

    def _create(self) -> None:
        """A new task, a share of them requiring review, so the successors
        that escalate.raise and control.supersede make carry it too."""
        rnd = self.rnd
        extra = {"review_required": True} if rnd.random() < 0.25 else {}
        r = self._send("task.create", "human:a", kind=rnd.choice(["a", "b"]),
                       input={"n": self._n}, assignee=rnd.choice(AGENTS), **extra)
        tid = self._result(r, "task_id")
        if tid:
            self.tasks.append(tid)

    def _miss(self) -> None:
        """One call the current state refuses, so error paths stay compared."""
        rnd = self.rnd
        tid = self._task()
        if tid is None:
            return
        rnd.choice([
            lambda: self._send("decide.approve", rnd.choice(HUMANS), task_id=tid,
                               comment="c"),
            lambda: self._send("control.resume", "human:a", task_id=tid),
            lambda: self._send("task.update", AGENTS[0], task_id=tid, state="paused"),
            lambda: self._send("abstain.declare", rnd.choice(HUMANS), task_id=tid,
                               reason="not mine"),
            lambda: self._send("handoff.accept", rnd.choice(HUMANS),
                               handoff_id="hnd_absent"),
            lambda: self._send("whisper.answer", rnd.choice(HUMANS),
                               whisper_id="whp_absent", answer="yes"),
            # Calls from a non-member to methods held to the membership floor,
            # and from a caller whose `from` is not a string.
            lambda: self._send("task.route", "human:outsider", task_id=tid,
                               candidates=list(AGENTS)),
            lambda: self._send("participant.leave", "human:outsider"),
            lambda: self._send("participant.leave", None),
            # task.update opens no review, and a range past the end of the log
            # still reports where the next read starts.
            lambda: self._send("task.update", self._assignee(tid), task_id=tid,
                               state="review_requested"),
            lambda: self._send("audit.read", "human:a",
                               range={"from_seq": 0, "to_seq": 100_000}),
            lambda: self._send("task.route", ["human:a"], task_id=tid,
                               candidates=list(AGENTS)),
        ])()

    def _plausible(self, name: str) -> object:
        """A well-formed value for a param, drawn from the workspace's state."""
        rnd = self.rnd
        members = [*HUMANS, *AGENTS]
        known = {
            "task_id": self.tasks, "original_task_id": self.tasks,
            "handoff_id": self.handoffs, "whisper_id": self.whispers,
            "deliberation_id": self.delibs, "to_snapshot_artefact_id": self.snapshots,
            "snapshot_artefact_id": self.snapshots, "snapshot_id": self.snapshots,
        }
        if name in known:
            return rnd.choice(known[name] or ["absent"])
        if name in ("to", "participants", "candidates"):
            return rnd.sample(members, 2)
        if name in ("assignee", "target_uri", "participant_uri", "uri",
                    "suggested_target", "default_escalation_target"):
            return rnd.choice(members)
        choices = {
            "type": ["human", "agent"], "state": ["in_progress", "declined", "completed"],
            "rule": ["any_one_approves", "quorum:2", "all_approve"],
            "scope": ["task", "participant", "workspace"],
            "vote": ["yea", "nay", "abstain"],
            "mode": ["shadow", "trial", "production"],
            "new_ceiling": ["trial", "production"], "mode_ceiling": ["trial", "production"],
        }
        if name in choices:
            return rnd.choice(choices[name])
        if name in ("input", "artefact", "output", "routing_hints", "artefact_routing_hints",
                    "capabilities", "filter", "range", "receipt", "weights", "veto"):
            return {"k": rnd.randint(0, 3)}
        if name in ("new_task", "successor_task"):
            return {"kind": "k", "input": {}, "assignee": AGENTS[0]}
        if name in ("tags", "policy_refs", "include", "what_to_restore", "context_links",
                    "accepted_task_ids", "profiles", "scopes"):
            return ["members"]
        if name == "diff":
            return [{"op": "replace", "path": "/text", "value": "y"}]
        if name in ("review_required", "request_revision", "intent_preserved", "veto_invoked"):
            return rnd.choice([True, False])
        if name in ("deadline_ms", "weight", "step_up_window_sec", "from_seq", "to_seq"):
            return rnd.randint(0, 5)
        if name == "tasks":
            return [{"task_id": rnd.choice(self.tasks or ["absent"])}]
        if name == "options":
            return [{"id": "a"}, {"id": "b"}]
        return rnd.choice(["text", "a note"])

    def _malformed(self) -> None:
        """A call with a param missing, or of the wrong type or shape.

        It starts from params a handler could accept and then breaks one or
        two of them, the workspace and the sender included now and then, so
        the checks a method makes before it changes anything are compared,
        and so is what it leaves behind when it refuses.
        """
        rnd = self.rnd
        method = rnd.choice(sorted(METHOD_PARAMS))
        names = METHOD_PARAMS[method]
        if rnd.random() < 0.05:
            # Something that is not a JSON-RPC call, or a call with no id.
            self._n += 1
            self._send_envelope("(not a call)", rnd.choice([
                None, [], "x", 5, {}, {"jsonrpc": "1.0", "id": f"e{self._n}", "method": method},
                {"jsonrpc": "2.0", "id": f"e{self._n}"},
                {"jsonrpc": "2.0", "id": f"e{self._n}", "method": rnd.choice(JUNK)},
                {"jsonrpc": "2.0", "method": "workspace.describe", "params": {"workspace": "w"}},
            ]))
            return
        if rnd.random() < 0.05:
            # params that are not an object at all, or absent.
            self._send_params(method, rnd.choice([..., None, [], "x", 5]))
            return
        params: dict = {"workspace": "w", "from": rnd.choice([*HUMANS, *AGENTS])}
        for name in names:
            if rnd.random() < 0.7:
                params[name] = self._plausible(name)
        targets = list(names) + ["workspace", "from"] * (1 if names else 3)
        for name in rnd.sample(targets, min(len(targets), rnd.randint(1, 2))):
            if rnd.random() < 0.2:
                params.pop(name, None)
            else:
                params[name] = rnd.choice(JUNK)
        self._send_params(method, params)

    def _assignee(self, tid: str) -> str:
        t = self.coord.get_workspace("w").tasks.get(tid)
        return t.assignee if t else AGENTS[0]

    def chain_head(self) -> str | None:
        return self.coord.get_workspace("w").chain_head


# Past every deadline the fuzzer sets, so a store replay lapses each whisper
# still pending, outside any call.
_LAPSE_AT = "2100-01-01T00:00:00.000Z"


def _replay_ts(envelopes: list[dict], store: str | None = None) -> dict:
    payload = json.dumps({"options": {"defaultProfiles": PROFILES},
                          "workspace": "w", "envelopes": envelopes,
                          **({"store": store, "lapse_at": _LAPSE_AT} if store else {})})
    proc = subprocess.run(
        ["npx", "tsx", str(_REPLAY)],
        input=payload, capture_output=True, text=True, cwd=str(_ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"replay.ts failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def _plain(coord: Coordinator) -> str:
    out = {}
    for ws_id, ws in coord.workspaces.items():
        data = asdict(ws)
        data["audit"] = [e.to_dict() for e in ws.audit]
        out[ws_id] = data
    return json.dumps(out, sort_keys=True, default=str)


def _drop_null(value):
    """A value as JSON holds it once nulls are left out, so a field one
    coordinator leaves unset and the other sets to null compare equal."""
    if isinstance(value, dict):
        return {k: _drop_null(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_null(v) for v in value]
    return value


def project(coord: Coordinator) -> dict:
    """Every workspace's state, by id, as replay.ts projects the TypeScript
    coordinator's, so the two can be compared after every call."""
    return {ws_id: _project_workspace(coord, ws_id) for ws_id in sorted(coord.workspaces)}


def _project_workspace(coord: Coordinator, workspace: str) -> dict | None:
    """One workspace's state in the shape replay.ts projects the TypeScript
    workspace to.

    The coordinators hold the same state in their own types: a Python history
    entry names its sender `from_`, a route decision keeps its extra fields
    apart, and an unset list is empty where TypeScript leaves it out. This
    maps those differences away and keeps every other difference.
    """
    ws = coord.workspaces.get(workspace)
    if ws is None:
        return None
    data = json.loads(json.dumps(asdict(ws), default=str))
    out: dict = {k: data[k] for k in (
        "id", "created", "state", "profiles", "mode", "mode_ceiling",
        "routing_policy_uri", "step_up_window_sec", "min_acr",
        "chain_head", "chain_enabled")}
    out["members"] = sorted(data["members"].values(), key=lambda m: str(m.get("uri")))
    tasks = []
    for t in data["tasks"].values():
        t["history"] = [{("from" if k == "from_" else k): v for k, v in h.items()}
                        for h in t.get("history") or []]
        review = t.get("review")
        if isinstance(review, dict):
            for d in review.get("decisions") or []:
                if isinstance(d, dict) and not d.get("tags"):
                    d.pop("tags", None)
        tasks.append(t)
    out["tasks"] = sorted(tasks, key=lambda t: str(t.get("id")))
    out["overrides"] = sorted(data["overrides"].values(), key=lambda o: str(o.get("id")))
    out["whispers"] = sorted(data["whispers"].values(), key=lambda w: str(w.get("id")))
    out["deliberations"] = sorted(data["deliberations"].values(), key=lambda d: str(d.get("id")))
    handoffs = []
    for h in data["handoffs"].values():
        if not h.get("accepted_task_ids"):
            h.pop("accepted_task_ids", None)
        handoffs.append(h)
    out["handoffs"] = sorted(handoffs, key=lambda h: str(h.get("id")))
    out["snapshots"] = sorted(data["snapshots"].values(), key=lambda s: str(s.get("id")))
    decisions = []
    for r in data["route_decisions"].values():
        extra = r.pop("extra", None) or {}
        decisions.append({**r, **extra, "kind": "route_decision"})
    out["route_decisions"] = sorted(decisions, key=lambda r: str(r.get("id")))
    out["idempotency_keys"] = data["idempotency_keys"]
    out["audit"] = [e.to_dict() for e in ws.audit]
    return _drop_null(json.loads(json.dumps(out, default=str)))


def _replay_py(envelopes: list[dict], store: str) -> tuple[list[dict], list[str], bool]:
    """The Python reference on a SQLite file: responses, store errors, restart."""
    options = dict(deterministic_ids=True, deterministic_clock=True,
                   enable_chain=True, default_profiles=PROFILES)
    errors: list[str] = []
    sq = SqliteStore(store)
    coord = Coordinator(CoordinatorOptions(
        **options, store=sq, on_store_error=lambda e, r: errors.append(str(e))))
    responses = [coord.dispatch(json.loads(json.dumps(e))) for e in envelopes]
    coord.check_whisper_lapses("w", _LAPSE_AT)
    before = _plain(coord)
    sq.close()
    reopened = SqliteStore(store)
    after = _plain(Coordinator(CoordinatorOptions(**options, store=reopened)))
    reopened.close()
    return responses, errors, before == after


def _entries(path: str) -> list:
    con = sqlite3.connect(path)
    try:
        return [(seq, json.loads(data)) for seq, data in con.execute(
            "SELECT seq, data FROM chap_entries WHERE workspace = 'w' ORDER BY seq")]
    finally:
        con.close()


def check_stores(seed: int, rec: "Recorder") -> tuple[bool, str]:
    """Both references on a SQLite file: nothing missed, the same entries."""
    with tempfile.TemporaryDirectory() as tmp:
        py_path, ts_path = str(Path(tmp) / "py.db"), str(Path(tmp) / "ts.db")
        responses, errors, same = _replay_py(rec.envelopes, py_path)
        if responses != rec.responses:
            return False, f"seed {seed}: the Python store changed a response"
        if errors or not same:
            return False, (f"seed {seed}: the Python store "
                           f"{'failed: ' + errors[0] if errors else 'lost state across a restart'}")
        ts = _replay_ts(rec.envelopes, ts_path)
        if ts["errors"] or not ts["restart_same"]:
            return False, (f"seed {seed}: the TypeScript store "
                           f"{'failed: ' + ts['errors'][0] if ts['errors'] else 'lost state across a restart'}")
        py_entries, ts_entries = _entries(py_path), _entries(ts_path)
        if py_entries != ts_entries:
            at = next((i for i, (a, b) in enumerate(zip(py_entries, ts_entries)) if a != b),
                      min(len(py_entries), len(ts_entries)))
            return False, (f"seed {seed}: the stores hold different audit entries from seq {at}\n"
                           f"  python: {json.dumps(py_entries[at][1]) if at < len(py_entries) else '<none>'}\n"
                           f"  ts:     {json.dumps(ts_entries[at][1]) if at < len(ts_entries) else '<none>'}")
    return True, ""


def _state_difference(py: object, ts: object, path: str = "") -> str:
    """The first place two projections differ, as a path and both values."""
    if isinstance(py, dict) and isinstance(ts, dict):
        for key in sorted(set(py) | set(ts)):
            if py.get(key, ...) != ts.get(key, ...):
                if key in py and key in ts:
                    return _state_difference(py[key], ts[key], f"{path}/{key}")
                return (f"  at {path}/{key}\n"
                        f"    python: {json.dumps(py.get(key, '<absent>'))}\n"
                        f"    ts:     {json.dumps(ts.get(key, '<absent>'))}")
    if isinstance(py, list) and isinstance(ts, list) and len(py) == len(ts):
        for i, (a, b) in enumerate(zip(py, ts)):
            if a != b:
                return _state_difference(a, b, f"{path}/{i}")
    return f"  at {path or '/'}\n    python: {json.dumps(py)}\n    ts:     {json.dumps(ts)}"


def _first_divergence(py: list[dict], ts: list[dict]) -> int | None:
    for i in range(min(len(py), len(ts))):
        if py[i] != ts[i]:
            return i
    if len(py) != len(ts):
        return min(len(py), len(ts))
    return None


def check_seed(seed: int, steps: int,
               tally: tuple[Counter, Counter] | None = None) -> tuple[bool, str]:
    rec = Recorder(seed, steps)
    if tally is not None:
        tally[0].update(rec.sent)
        tally[1].update(rec.ok)
    altered = rec.recording_matches_what_was_sent()
    if altered:
        return False, (
            f"seed {seed}: the coordinator altered the envelope it recorded\n"
            f"  methods: {', '.join(sorted(set(altered)))}\n"
            "  A recorded envelope must be the one the client sent, or a "
            "signature over it no longer verifies.")
    faults = [i for i, r in enumerate(rec.responses)
              if r.get("error", {}).get("code") == -32603]
    if faults:
        i = faults[0]
        return False, (
            f"seed {seed}: the Python reference answered envelope {i} with -32603\n"
            f"  envelope: {json.dumps(rec.envelopes[i])}\n"
            "  -32603 is a fault in the coordinator: every refusal of a call has "
            "its own code.")
    ts = _replay_ts(rec.envelopes)
    ts_responses = ts["responses"]
    idx = _first_divergence(rec.responses, ts_responses)
    if idx is not None:
        env = rec.envelopes[idx] if idx < len(rec.envelopes) else None
        return False, (
            f"seed {seed}: responses diverge at envelope {idx}\n"
            f"  envelope: {json.dumps(env)}\n"
            f"  python:   {json.dumps(rec.responses[idx]) if idx < len(rec.responses) else '<none>'}\n"
            f"  ts:       {json.dumps(ts_responses[idx]) if idx < len(ts_responses) else '<none>'}")
    faults = [i for i, r in enumerate(ts_responses)
              if (r.get("error") or {}).get("code") == -32603]
    if faults:
        i = faults[0]
        return False, (
            f"seed {seed}: the TypeScript reference answered envelope {i} with -32603\n"
            f"  envelope: {json.dumps(rec.envelopes[i])}")
    idx = _first_divergence(rec.states, ts["states"])
    if idx is not None:
        return False, (
            f"seed {seed}: state diverges after envelope {idx}\n"
            f"  envelope: {json.dumps(rec.envelopes[idx])}\n"
            f"  response: {json.dumps(rec.responses[idx])}\n"
            f"{_state_difference(rec.states[idx], ts['states'][idx])}")
    if rec.chain_head() != ts["chain_head"]:
        return False, (
            f"seed {seed}: chain heads diverge after {len(rec.envelopes)} envelopes\n"
            f"  python: {rec.chain_head()}\n"
            f"  ts:     {ts['chain_head']}")
    stored, msg = check_stores(seed, rec)
    if not stored:
        return False, msg
    return True, f"seed {seed}: ok ({len(rec.envelopes)} envelopes)"


def _stats_table(sent: Counter, ok: Counter) -> str:
    """Per method, how many envelopes were sent and how many were accepted.

    A method whose calls are nearly all refused is being checked at the edge
    of its profile rather than in the middle of it, which is what this table
    makes visible.
    """
    width = max((len(m) for m in sent), default=6)
    lines = [f"{'method'.ljust(width)}  {'sent':>5} {'accepted':>9} {'rate':>6}",
             "-" * (width + 24)]
    for method in sorted(sent):
        accepted = ok[method]
        rate = accepted / sent[method]
        lines.append(f"{method.ljust(width)}  {sent[method]:5} {accepted:9} {rate:6.0%}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seeds", type=int, default=50, help="check seeds 0..N-1")
    p.add_argument("--seed", type=int, help="check a single seed")
    p.add_argument("--steps", type=int, default=40, help="random steps per run")
    p.add_argument("--stats", action="store_true",
                   help="print sent and succeeded per method, so coverage is "
                        "visible in the log rather than assumed")
    args = p.parse_args(argv)

    seeds = [args.seed] if args.seed is not None else range(args.seeds)
    tally: tuple[Counter, Counter] = (Counter(), Counter())
    failures = 0
    for seed in seeds:
        ok, msg = check_seed(seed, args.steps, tally)
        if not ok:
            failures += 1
            print(msg)
    total = 1 if args.seed is not None else args.seeds
    if args.stats:
        print(_stats_table(*tally))
    print(f"\n{total - failures}/{total} seeds agreed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
