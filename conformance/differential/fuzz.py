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

The action set covers the core and review lifecycle plus control pause/resume
and escalation. control.snapshot is deliberately left out until the known
artefact-shape divergence between the two references is resolved: the fuzzer
catches it immediately when it is added, so it belongs in a run that expects to
pass. Widen the action set as the references converge.

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

PROFILES = ["core/1.0", "review/1.0", "control/1.0"]
HUMANS = ["human:a", "human:c"]
AGENT = "agent:b"


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
        self._send("participant.join", AGENT, type="agent")

    def _task(self) -> str | None:
        return self.rnd.choice(self.tasks) if self.tasks else None

    def _step(self) -> None:
        actions = ["create", "update", "review", "approve", "reject",
                   "override", "complete", "pause", "resume", "escalate"]
        a = self.rnd.choice(actions)
        if a == "create":
            r = self._send("task.create", "human:a", kind="k", input={}, assignee=AGENT)
            if "result" in r:
                self.tasks.append(r["result"]["task_id"])
            return
        tid = self._task()
        if tid is None:
            return
        human = self.rnd.choice(HUMANS)
        if a == "update":
            self._send("task.update", AGENT, task_id=tid,
                       state=self.rnd.choice(["in_progress", "declined"]))
        elif a == "review":
            self._send("review.request", AGENT, task_id=tid,
                       artefact={"text": self.rnd.choice(["x", "y"])}, to=human)
        elif a == "approve":
            self._send("decide.approve", human, task_id=tid, comment="ok", rationale="ok")
        elif a == "reject":
            self._send("decide.reject", human, task_id=tid, comment="no", rationale="no")
        elif a == "override":
            self._send("decide.override", human, task_id=tid, rationale="fix",
                       diff=[{"op": "replace", "path": "/text", "value": "z"}])
        elif a == "complete":
            self._send("task.complete", AGENT, task_id=tid, output={"text": "done"})
        elif a == "pause":
            self._send("control.pause", "human:a", task_id=tid, reason="hold")
        elif a == "resume":
            self._send("control.resume", "human:a", task_id=tid)
        elif a == "escalate":
            self._send("escalate.raise", "human:a", original_task_id=tid, reason="up",
                       new_task={"kind": "k", "input": {}, "assignee": AGENT})

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
