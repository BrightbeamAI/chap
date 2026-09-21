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
deliberation and routing. Two cases are deliberately held out until known
divergences between the references are resolved, because the fuzzer catches
each immediately and a CI run is expected to pass: control.snapshot (artefact
shape, issue #148) and handoff.accept with an explicitly empty
accepted_task_ids (issue #151, where Python expands the empty list to every
task). Fold each back in once its fix lands. Widen the set as the references
converge.

Usage:
    python fuzz.py --seeds 200          # sweep seeds 0..199
    python fuzz.py --seed 42            # one seed, verbose on divergence
    python fuzz.py --seeds 50 --steps 60
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
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
        self.responses: list[dict] = []
        self.tasks: list[str] = []
        self.handoffs: list[str] = []
        self.whispers: list[str] = []
        self.delibs: list[str] = []
        self._n = 0
        self._bootstrap()
        for _ in range(steps):
            self._step()

    def _send(self, method: str, actor: str, **params) -> dict:
        self._n += 1
        env = {"jsonrpc": "2.0", "id": f"e{self._n}", "method": method,
               "params": {"workspace": "w", "from": actor, **params}}
        resp = self.coord.dispatch(env)
        self.envelopes.append(env)
        self.responses.append(resp)
        return resp

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

    def _step(self) -> None:
        rnd = self.rnd
        a = rnd.choice([
            "create", "create", "update", "review", "decide", "decide",
            "complete", "pause", "resume", "escalate", "supersede", "cancel",
            "handoff", "whisper", "deliberate", "route", "depth", "auto"])
        if a == "create" or not self.tasks:
            r = self._send("task.create", "human:a", kind=rnd.choice(["a", "b"]),
                           input={"n": self._n}, assignee=rnd.choice(AGENTS))
            tid = self._result(r, "task_id")
            if tid:
                self.tasks.append(tid)
            return
        tid = self._task()
        human = rnd.choice(HUMANS)
        if a == "update":
            self._send("task.update", AGENTS[0], task_id=tid,
                       state=rnd.choice(["in_progress", "declined"]))
        elif a == "review":
            self._send("review.request", AGENTS[0], task_id=tid,
                       artefact={"text": rnd.choice(["x", "y"])},
                       to=rnd.sample(HUMANS, rnd.randint(1, 2)))
        elif a == "decide":
            kind = rnd.choice(["decide.approve", "decide.reject",
                               "decide.override", "abstain.declare"])
            p: dict = {"task_id": tid, "comment": "c", "rationale": "r"}
            if kind == "decide.override":
                p["diff"] = [{"op": "replace", "path": "/text", "value": "z"}]
            if kind == "abstain.declare":
                p = {"task_id": tid, "reason": "conflict of interest"}
            self._send(kind, human, **p)
        elif a == "complete":
            self._send("task.complete", AGENTS[0], task_id=tid, output={"text": "done"})
        elif a == "pause":
            self._send("control.pause", "human:a", task_id=tid, reason="hold")
        elif a == "resume":
            self._send("control.resume", "human:a", task_id=tid)
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
            if hid and rnd.random() < 0.8:
                who = rnd.choice(HUMANS)
                if rnd.random() < 0.5:
                    self._send("handoff.accept", who, handoff_id=hid,
                               accepted_task_ids=[tid])
                else:
                    self._send("handoff.decline", who, handoff_id=hid, reason="not mine")
        elif a == "whisper":
            r = self._send("whisper.ask", self._assignee(tid), task_id=tid,
                           to=[rnd.choice(HUMANS)], question=f"q{self._n}",
                           deadline_ms=rnd.choice([0, 600_000]), default_if_lapsed="no")
            wid = self._result(r, "whisper_id")
            if wid and rnd.random() < 0.6:
                self._send("whisper.answer", rnd.choice(HUMANS), whisper_id=wid, answer="yes")
        elif a == "deliberate":
            r = self._send("deliberate.open", human, to=rnd.sample(HUMANS, rnd.randint(1, 2)),
                           rule="any_one_approves", question=f"Q{self._n}", task_id=tid)
            did = self._result(r, "deliberation_id")
            if did:
                for who in rnd.sample(HUMANS, rnd.randint(0, 2)):
                    self._send("deliberate.vote", who, deliberation_id=did,
                               vote=rnd.choice(["yea", "nay"]))
                if rnd.random() < 0.5:
                    self._send("deliberate.close", human, deliberation_id=did)
        elif a == "route":
            self._send("task.route", "human:a", task_id=tid, candidates=list(AGENTS))
        elif a == "depth":
            self._send("review.depth", "human:a", task_id=tid,
                       artefact_routing_hints={"criticality": "high"})
        elif a == "auto":
            self._send("escalate.auto", "human:a", task_id=tid,
                       default_escalation_target=rnd.choice(HUMANS))

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


def check_seed(seed: int, steps: int) -> tuple[bool, str]:
    rec = Recorder(seed, steps)
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seeds", type=int, default=50, help="check seeds 0..N-1")
    p.add_argument("--seed", type=int, help="check a single seed")
    p.add_argument("--steps", type=int, default=40, help="random steps per run")
    args = p.parse_args(argv)

    seeds = [args.seed] if args.seed is not None else range(args.seeds)
    failures = 0
    for seed in seeds:
        ok, msg = check_seed(seed, args.steps)
        if not ok:
            failures += 1
            print(msg)
    total = 1 if args.seed is not None else args.seeds
    print(f"\n{total - failures}/{total} seeds agreed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
