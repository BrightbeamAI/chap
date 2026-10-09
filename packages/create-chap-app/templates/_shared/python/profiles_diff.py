"""diff-profiles: the same workload under two profile sets, side by side.

Each step is one CHAP call. The table shows what each profile set did with
it: accepted, or refused with which code. The rows that differ are what a
profile changes. The decisions here are scripted, because this is a
comparison and no person is at the desk; in the project itself the decision
is always made in the desk.

The workload runs in-process on an in-memory coordinator, so it needs no
server and no model. The same steps, with the same ids, exist in the Node
templates, and the explorer merges both into docs/profile-explorer.md.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.store import MemoryStore

AGENT = "agent:drafter"
HUMAN = "human:reviewer"
SECOND = "human:second"

BOOTSTRAP_METHODS = ("workspace.create", "participant.join")


def _held(state: dict, key: str, fallback: str = "none") -> Any:
    """The id a step kept, or the fallback when the step that kept it was refused."""
    value = state.get(key)
    return value if value is not None else fallback


# The steps. ``params`` receives the state the earlier steps kept.
STEPS: list[dict[str, Any]] = [
    {"id": "task.create.trial", "label": "The agent opens a trial-mode task that requires review",
     "method": "task.create", "from": AGENT, "keep": "task",
     "params": lambda s: {"kind": "draft", "input": {"n": 1}, "assignee": AGENT, "mode": "trial", "review_required": True}},
    {"id": "task.create.above-ceiling", "label": "The agent opens a production-mode task above a trial ceiling",
     "method": "task.create", "from": AGENT,
     "params": lambda s: {"kind": "draft", "input": {}, "assignee": AGENT, "mode": "production"}},
    {"id": "task.complete", "label": "The agent submits the task's output",
     "method": "task.complete", "from": AGENT,
     "params": lambda s: {"task_id": s.get("task"), "output": {"body": "draft"}}},
    {"id": "decide.approve.self", "label": "The agent approves its own work",
     "method": "decide.approve", "from": AGENT,
     "params": lambda s: {"task_id": s.get("task")}},
    {"id": "decide.approve.human", "label": "The reviewer approves the work",
     "method": "decide.approve", "from": HUMAN,
     "params": lambda s: {"task_id": s.get("task")}},
    {"id": "task.create.trial.plain", "label": "The agent opens a trial-mode task without asking for review",
     "method": "task.create", "from": AGENT, "keep": "plain",
     "params": lambda s: {"kind": "draft", "input": {"n": 2}, "assignee": AGENT, "mode": "trial"}},
    {"id": "task.complete.plain", "label": "The agent submits that task's output",
     "method": "task.complete", "from": AGENT,
     "params": lambda s: {"task_id": s.get("plain"), "output": {"body": "draft"}}},
    {"id": "task.create.unsigned", "label": "An unsigned call from the reviewer",
     "method": "task.create", "from": HUMAN, "unsigned": True,
     "params": lambda s: {"kind": "note", "input": {}, "assignee": HUMAN}},
    {"id": "whisper.ask", "label": "The agent asks the reviewer a quick question with a default",
     "method": "whisper.ask", "from": AGENT, "keep": "whisper",
     "params": lambda s: {"task_id": s.get("task"), "to": [HUMAN], "question": "Send now?", "deadline_ms": 1, "default_if_lapsed": "yes"}},
    {"id": "whisper.answer.stranger", "label": "Someone the question was not put to answers it",
     "method": "whisper.answer", "from": SECOND,
     "params": lambda s: {"whisper_id": _held(s, "whisper"), "answer": "no"}},
    {"id": "deliberate.open", "label": "The reviewer opens a vote under quorum:2",
     "method": "deliberate.open", "from": HUMAN, "keep": "deliberation",
     "params": lambda s: {"task_id": s.get("task"), "to": [HUMAN, SECOND], "rule": "quorum:2", "question": "Ship it?"}},
    {"id": "deliberate.close.one-vote", "label": "The vote closes after one yea",
     "method": "deliberate.close", "from": HUMAN,
     "params": lambda s: {"deliberation_id": _held(s, "deliberation")},
     "before": lambda s: {"method": "deliberate.vote", "from": HUMAN,
                          "params": {"deliberation_id": _held(s, "deliberation"), "vote": "yea"}}},
    {"id": "control.pause.agent", "label": "The reviewer pauses the agent",
     "method": "control.pause", "from": HUMAN,
     "params": lambda s: {"scope": "participant", "participant_uri": AGENT, "reason": "hold"}},
    {"id": "task.create.paused", "label": "A task is assigned to the paused agent",
     "method": "task.create", "from": HUMAN,
     "params": lambda s: {"kind": "draft", "input": {}, "assignee": AGENT}},
    {"id": "handoff.propose", "label": "The reviewer hands a task to the second reviewer",
     "method": "handoff.propose", "from": HUMAN,
     "params": lambda s: {"to": SECOND, "tasks": [{"task_id": s.get("task"), "summary": "yours"}]},
     "before": lambda s: {"method": "task.create", "from": HUMAN, "keep": "task",
                          "params": {"kind": "note", "input": {}, "assignee": HUMAN}}},
    {"id": "task.route", "label": "The agent asks the coordinator to choose an assignee",
     "method": "task.route", "from": AGENT,
     "params": lambda s: {"task_id": s.get("task"), "candidates": [HUMAN, SECOND]}},
    {"id": "audit.verify_chain", "label": "Anyone verifies the chain",
     "method": "audit.verify_chain", "from": HUMAN,
     "params": lambda s: {}},
]


def profile_options(profiles: list[str]) -> dict:
    return {
        "enable_chain": "audit-scitt/1.0" in profiles,
        "require_signatures": "security-signed/1.0" in profiles,
    }


class Signer:
    """An Ed25519 signer for one participant, with a key derived from its URI."""

    def __init__(self, uri: str):
        from chap_coordinator import crypto
        self._crypto = crypto
        self.uri = uri
        self.key = crypto.derive_private_key(uri)
        self.public_jwk = crypto.public_jwk(uri, self.key)
        self.kid = self.public_jwk["kid"]

    def sign(self, envelope: dict) -> dict:
        """The envelope with its ``sig`` set. The input is not changed."""
        from chap_coordinator.canonical import canonicalize
        unsigned = {k: v for k, v in envelope.items() if k != "sig"}
        sig = self._crypto.sign(canonicalize(unsigned), self.key, self.kid)
        return {**unsigned, "sig": sig}


def make_signers(make_signer: Callable[[str], Any] = Signer) -> dict[str, Any]:
    """Signers for the three participants."""
    return {uri: make_signer(uri) for uri in (HUMAN, SECOND, AGENT)}


def _kept_id(result: Any, keep: str) -> Any:
    if not isinstance(result, dict):
        return None
    for key in ("task_id", "whisper_id", "deliberation_id", "new_task_id", f"{keep}_id"):
        if result.get(key) is not None:
            return result[key]
    return None


def run_workload(profiles: list[str], workspace: str = "wsp_diff",
                 signers: dict[str, Any] | None = None) -> list[dict]:
    """Run the workload under one profile set. Returns one row per step with its
    outcome: {status: "ok", result} or {status: "refused", code, message}."""
    coord = Coordinator(CoordinatorOptions(
        store=MemoryStore(), default_profiles=list(profiles), **profile_options(profiles)))
    signed = "security-signed/1.0" in profiles
    counter = {"n": 0}

    def send(method: str, sender: str, params: dict, unsigned: bool = False) -> dict:
        counter["n"] += 1
        clean = {k: v for k, v in params.items() if v is not None}
        env = {"jsonrpc": "2.0", "id": f"d-{counter['n']}", "method": method,
               "params": {"workspace": workspace, "from": sender, **clean}}
        if signed and not unsigned and signers and method not in BOOTSTRAP_METHODS:
            env = signers[sender].sign(env)
        return coord.dispatch(env)

    def outcome(r: dict) -> dict:
        if "error" in r:
            return {"status": "refused", "code": r["error"]["code"], "message": r["error"]["message"]}
        return {"status": "ok", "result": r.get("result")}

    create = send("workspace.create", HUMAN, {"profiles": list(profiles), "mode": "trial", "mode_ceiling": "trial"})
    if "error" in create:
        raise RuntimeError(f"workspace.create under {','.join(profiles)}: {create['error']['message']}")
    for uri, type_ in ((HUMAN, "human"), (SECOND, "human"), (AGENT, "agent")):
        params: dict[str, Any] = {"type": type_, "role": "reviewer" if type_ == "human" else "drafter"}
        if signed and signers:
            params["jwks"] = {"keys": [signers[uri].public_jwk]}
        r = send("participant.join", uri, params)
        if "error" in r:
            raise RuntimeError(f"participant.join {uri}: {r['error']['message']}")

    state: dict[str, Any] = {}
    rows = []
    for step in STEPS:
        if step.get("before"):
            b = step["before"](state)
            r = send(b["method"], b["from"], b["params"])
            if b.get("keep") and "error" not in r:
                state[b["keep"]] = _kept_id(r.get("result"), b["keep"])
        r = send(step["method"], step["from"], step["params"](state), unsigned=bool(step.get("unsigned")))
        if step.get("keep") and "error" not in r:
            state[step["keep"]] = _kept_id(r.get("result"), step["keep"])
        rows.append({"id": step["id"], "label": step["label"], "method": step["method"], "outcome": outcome(r)})
    return rows


def summary(o: dict) -> str:
    if o.get("status") != "ok":
        return f"refused {o.get('code')}"
    result = o.get("result")
    state = result.get("state") if isinstance(result, dict) else None
    return f"accepted, {state}" if isinstance(state, str) else "accepted"


def compare(a: list[dict], b: list[dict]) -> list[dict]:
    return [{"id": row["id"], "label": row["label"], "method": row["method"],
             "a": row["outcome"], "b": b[i]["outcome"],
             "differs": summary(row["outcome"]) != summary(b[i]["outcome"])}
            for i, row in enumerate(a)]


def render_table(set_a: list[str], set_b: list[str], rows: list[dict]) -> str:
    width = max([len(r["label"]) for r in rows] + [10])
    head = f"{'Step'.ljust(width)}  {','.join(set_a).ljust(22)}  {','.join(set_b)}"
    lines = [head, "-" * len(head)]
    for r in rows:
        mark = "   <- differs" if r["differs"] else ""
        lines.append(f"{r['label'].ljust(width)}  {summary(r['a']).ljust(22)}  {summary(r['b'])}{mark}")
    return "\n".join(lines)


def _split(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


def parse_args(argv: list[str], defaults: list[str]) -> dict:
    """Parse ``--profiles a,b --against c,d --json`` from argv."""
    out: dict[str, Any] = {"profiles": list(defaults), "against": ["core/1.0"], "json": False}
    i = 0
    while i < len(argv):
        if argv[i] == "--profiles":
            i += 1
            out["profiles"] = _split(argv[i])
        elif argv[i] == "--against":
            i += 1
            out["against"] = _split(argv[i])
        elif argv[i] == "--json":
            out["json"] = True
        i += 1
    return out


def main(template: str, defaults: list[str], argv: list[str] | None = None,
         language: str = "python", make_signer: Callable[[str], Any] = Signer) -> None:
    args = parse_args(sys.argv[1:] if argv is None else argv, defaults)
    need_signers = any("security-signed/1.0" in p for p in (args["profiles"], args["against"]))
    signers = make_signers(make_signer) if need_signers else None
    a = run_workload(args["profiles"], signers=signers)
    b = run_workload(args["against"], signers=signers)
    rows = compare(a, b)
    if args["json"]:
        print(json.dumps({"template": template, "language": language,
                          "sets": {"a": args["profiles"], "b": args["against"]}, "rows": rows},
                         indent=2, ensure_ascii=False))
    else:
        print(f"Workload: {template}. Scripted decisions, for comparison only.\n")
        print(render_table(args["profiles"], args["against"], rows))


__all__ = ["AGENT", "HUMAN", "SECOND", "STEPS", "Signer", "compare", "main",
           "make_signers", "parse_args", "render_table", "run_workload", "summary"]
