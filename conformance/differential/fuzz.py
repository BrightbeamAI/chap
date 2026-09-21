"""
Differential conformance fuzzer for the two reference coordinators.

CHAP's headline guarantee is that two independently-authored coordinators
produce byte-identical results (SPECIFICATION.md). Static vectors check that on
hand-written cases; this checks it on random ones.

For each seed it drives the Python reference with a random sequence of legal
calls, records the concrete envelopes it accepted (ids are server-minted, so a
sequence cannot be written in advance), replays that exact sequence into the
TypeScript reference in a subprocess, and asserts the two agree on every
response and on the audit-chain head. A subprocess boundary is used rather than
HTTP so no serialisation layer masks or invents a difference.

The action set covers the core and review lifecycle, control, handoff, whisper,
deliberation and routing, including canonical control.snapshot artefacts and
subsequent rollback.

Each envelope is recorded as the client sent it, taken before dispatch, and
every seed also asserts that dispatch left it untouched. A coordinator records
what it received: a handler that writes into the request makes the recorded
envelope differ from the signed one, and would otherwise be replayed into the
other reference as though both had produced it.

Action selection reads the workspace as it stands: decisions go to tasks under
review and to reviewers the review named, resumes go to paused tasks, handoffs
are resolved by the participant they were offered to, votes come from
participants who have not voted. A share of the steps is a deliberate miss, so
the refusals are compared as well as the successes. Overrides draw from the
whole JSON Patch operation set against documents with arrays and nesting,
including the `1e1` and `1_0` index tokens behind #103.

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
import subprocess
import sys
from collections import Counter
from pathlib import Path

from chap_coordinator import Coordinator, CoordinatorOptions

_ROOT = Path(__file__).resolve().parents[2]
_REPLAY = _ROOT / "conformance" / "differential" / "replay.ts"

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "handoff/1.0",
            "whisper/1.0", "deliberation/1.0", "routing/1.0"]
HUMANS = ["human:a", "human:c"]
AGENTS = ["agent:b", "agent:d"]


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

    def _send(self, method: str, actor: str, **params) -> dict:
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
                "whisper", "deliberate", "snapshot", "miss"]
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
            r = self._send("task.create", "human:a", kind=rnd.choice(["a", "b"]),
                           input={"n": self._n}, assignee=rnd.choice(AGENTS))
            tid = self._result(r, "task_id")
            if tid:
                self.tasks.append(tid)
            return
        human = rnd.choice(HUMANS)

        if a == "miss":
            # A call aimed at a task that is in the wrong state for it, so the
            # refusals stay compared as well as the successes.
            self._miss()
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
            r = self._send("task.create", "human:a", kind=rnd.choice(["a", "b"]),
                           input={"n": self._n}, assignee=rnd.choice(AGENTS))
            tid = self._result(r, "task_id")
            if tid:
                self.tasks.append(tid)
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
            r = self._send("escalate.raise", "human:a", original_task_id=tid, reason="up",
                           new_task={"kind": "k", "input": {}, "assignee": AGENTS[0]})
            nt = self._result(r, "new_task_id")
            if nt:
                self.tasks.remove(tid)
                self.tasks.append(nt)
        elif a == "supersede":
            r = self._send("control.supersede", "human:a", task_id=tid, reason="redo",
                           successor_task={"kind": "v2", "input": {}, "assignee": AGENTS[0]})
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
        ])()

    def _assignee(self, tid: str) -> str:
        t = self.coord.get_workspace("w").tasks.get(tid)
        return t.assignee if t else AGENTS[0]

    def chain_head(self) -> str | None:
        return self.coord.get_workspace("w").chain_head


def _replay_ts(envelopes: list[dict]) -> dict:
    payload = json.dumps({"options": {"defaultProfiles": PROFILES},
                          "workspace": "w", "envelopes": envelopes})
    proc = subprocess.run(
        ["npx", "tsx", str(_REPLAY)],
        input=payload, capture_output=True, text=True, cwd=str(_ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"replay.ts failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


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
    if rec.chain_head() != ts["chain_head"]:
        return False, (
            f"seed {seed}: chain heads diverge after {len(rec.envelopes)} envelopes\n"
            f"  python: {rec.chain_head()}\n"
            f"  ts:     {ts['chain_head']}")
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
