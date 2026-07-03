#!/usr/bin/env python3
"""
Scenario 1: the solo dev who cannot remember what they overrode.

IN_PRACTICE.md S1. A single developer runs a code-review bot (an ``agent:``
participant here) over their pull requests. They accept most of its
comments, reject some, and edit a few before merging. Every decision is
recorded as a CHAP envelope on a hash-linked audit chain, so months later
the override history is a queryable, tamper-evident record instead of a
vague feeling that the bot is "pretty good."

This is the canonical worked example for the scenarios/ directory. It uses
CHAP core plus two profiles that ship with the reference coordinator
(review/1.0, audit-scitt/1.0). No framework, no network, no external
services, and no dependencies beyond the standard library.

Run it:

    # from a clone of the repo, nothing to install:
    python3 scenario.py

    # or, once chap-coordinator is on PyPI:
    pip install chap-coordinator && python3 scenario.py

It prints three things, in order:
  1. a check that the audit chain is intact, and that a tamper would be
     caught: why this beats a spreadsheet;
  2. a full reconstruction of one past override: exactly what you changed
     on one PR, and why;
  3. the override learning report: the tag breakdown you feed back into the
     bot's next prompt.
"""
from __future__ import annotations

import copy
import sys
from collections import Counter
from pathlib import Path

# Import the coordinator. Prefer an installed chap-coordinator; fall back to
# the in-repo package so a fresh clone runs with nothing installed.
try:
    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_coordinator.canonical import canonicalize, sha256_hex
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "coordinator-py"))
    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_coordinator.canonical import canonicalize, sha256_hex


WORKSPACE = "wsp_my_reviews"
ME = "human:me@local"          # the developer, and the only reviewer
BOT = "agent:cursor@local"     # the code-review bot whose comments are reviewed
PROFILES = ["core/1.0", "review/1.0", "audit-scitt/1.0"]
GENESIS = "sha256:" + "0" * 64


def main() -> None:
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True,     # stable output across runs
        deterministic_clock=True,
        default_profiles=PROFILES,
    ))

    def send(method: str, **params) -> dict:
        resp = coord.dispatch({
            "jsonrpc": "2.0", "id": method, "method": method, "params": params,
        })
        if "error" in resp:
            raise SystemExit(f"{method} failed: {resp['error']}")
        return resp["result"]

    build_history(send)

    entries = send("audit.read", workspace=WORKSPACE)["entries"]
    print_integrity(entries)
    print_reconstruction(send)
    print_override_report(send)


# ---------------------------------------------------------------------------
# Build a few months of reviews: a mix of accept / reject / edit.
# ---------------------------------------------------------------------------

def build_history(send) -> None:
    send("workspace.create", workspace=WORKSPACE, profiles=PROFILES)
    send("participant.join", workspace=WORKSPACE, **{"from": ME}, type="human", role="reviewer")
    send("participant.join", workspace=WORKSPACE, **{"from": BOT}, type="agent", role="drafter")

    # (PR, decision, diff, rationale, tags). A representative slice; a real
    # workspace would hold hundreds. The shape is what matters.
    reviews = [
        ("PR-471", "approve", None, "Legit missing null check; accepted.", []),
        ("PR-472", "override",
         [{"op": "replace", "path": "/comments/0/severity", "value": "info"}],
         "Bot flags unused parameter on every event handler. False positive: "
         "handlers conform to a framework signature.",
         ["false-positive", "framework-pattern-misread"]),
        ("PR-473", "reject", None, "Style nit the team explicitly does not enforce.",
         ["cosmetic-pref"]),
        ("PR-474", "override",
         [{"op": "replace", "path": "/comments/0/severity", "value": "info"}],
         "Same framework-signature false positive as PR-472.",
         ["false-positive", "framework-pattern-misread"]),
        ("PR-475", "approve", None, "Good catch on the race condition.", []),
        ("PR-476", "override",
         [{"op": "remove", "path": "/comments/1"}],
         "Second comment is a cosmetic preference; dropped it, kept the real one.",
         ["cosmetic-pref"]),
        ("PR-477", "override",
         [{"op": "replace", "path": "/comments/0/severity", "value": "info"}],
         "Framework-signature false positive again.",
         ["false-positive", "framework-pattern-misread"]),
        ("PR-478", "reject", None, "Duplicate of an existing lint rule.", ["duplicate"]),
    ]

    for pr, decision, diff, rationale, tags in reviews:
        created = send("task.create", workspace=WORKSPACE, **{"from": ME},
                       kind="code_review", assignee=BOT, input={"pr": pr})
        task_id = created["task_id"]
        send("task.update", workspace=WORKSPACE, **{"from": BOT},
             task_id=task_id, state="in_progress")

        artefact = {"pr": pr, "comments": [
            {"path": "src/handler.ts", "severity": "warning", "note": "unused parameter"},
            {"path": "src/util.ts", "severity": "info", "note": "prefer const"},
        ]}
        send("task.complete", workspace=WORKSPACE, **{"from": BOT},
             task_id=task_id, output=artefact)
        send("review.request", workspace=WORKSPACE, **{"from": BOT},
             task_id=task_id, to=[ME], artefact=artefact)

        if decision == "approve":
            send("decide.approve", workspace=WORKSPACE, **{"from": ME},
                 task_id=task_id, comment=rationale, tags=tags)
        elif decision == "reject":
            send("decide.reject", workspace=WORKSPACE, **{"from": ME},
                 task_id=task_id, comment=rationale, tags=tags)
        else:
            send("decide.override", workspace=WORKSPACE, **{"from": ME},
                 task_id=task_id, diff=diff, rationale=rationale, tags=tags,
                 intent_preserved=True)


# ---------------------------------------------------------------------------
# 1. Integrity: the chain is hash-linked, so it is tamper-evident.
# ---------------------------------------------------------------------------

def _verify(entries):
    """Re-walk the hash chain the way an auditor would.

    Each entry carries the prev_hash it was linked against; the next link is
    sha256(JCS(envelope) || prev_hash). If any envelope was altered after
    the fact, the recomputed links stop matching. Returns (intact, seq of
    the first broken entry or None).
    """
    running = GENESIS
    for e in entries:
        if e.get("prev_hash") != running:
            return False, e["seq"]
        running = sha256_hex(canonicalize(e["envelope"]) + running.encode("utf-8"))
    return True, None


def print_integrity(entries) -> None:
    print("1. Is this record trustworthy?")
    print("=" * 52)
    intact, _ = _verify(entries)
    print(f"   Audit entries on the chain: {len(entries)}")
    print(f"   Chain verifies (hash-linked, intact): {intact}")

    # Show that tampering is caught: flip one recorded decision on a COPY and
    # re-verify. The real chain is untouched.
    forged = copy.deepcopy(entries)
    for e in forged:
        if e["envelope"]["method"] == "decide.reject":
            e["envelope"]["params"]["from"] = "human:someone-else@local"
            break
    intact_after, broken_seq = _verify(forged)
    print(f"   If one decision were quietly edited: verifies = {intact_after}"
          f" (breaks at seq {broken_seq})")
    print("   -> the history cannot be rewritten after the fact without")
    print("      detection. A plain log or spreadsheet gives no such guarantee.")
    print()


# ---------------------------------------------------------------------------
# 2. Reconstruction: exactly what you changed on one PR, and why.
# ---------------------------------------------------------------------------

def print_reconstruction(send) -> None:
    overrides = send("audit.read", workspace=WORKSPACE,
                     filter={"method": "decide.override"})["entries"]
    task_to_pr = _task_to_pr(send)

    print('2. Three months later: what did I change on PR-472, and why?')
    print("=" * 52)
    target = None
    for e in overrides:
        if task_to_pr.get(e["envelope"]["params"]["task_id"]) == "PR-472":
            target = e
            break
    if target is None:
        target = overrides[0]
    p = target["envelope"]["params"]
    print(f"   reviewer:          {p['from']}")
    print(f"   intent preserved:  {p.get('intent_preserved')}")
    print(f"   edit (JSON Patch): {p['diff']}")
    print(f"   rationale:         {p['rationale']}")
    print(f"   tags:              {p.get('tags')}")
    print("   -> not 'the human changed something', but the precise diff, the")
    print("      reason, and the category, recovered from the record.")
    print()


# ---------------------------------------------------------------------------
# 3. The learning report: the tag breakdown you feed back into the bot.
# ---------------------------------------------------------------------------

def print_override_report(send) -> None:
    overrides = send("audit.read", workspace=WORKSPACE,
                     filter={"method": "decide.override"})["entries"]
    total = len(overrides)
    counts: Counter = Counter()
    for e in overrides:
        for tag in (e["envelope"]["params"].get("tags") or []):
            counts[tag] += 1

    print("3. Override Learning Report (wsp_my_reviews)")
    print("=" * 52)
    print(f"   Total overrides: {total}")
    print("   By tag:")
    width = max((len(t) for t in counts), default=0)
    for tag, n in counts.most_common():
        bar = "#" * n
        pct = (n / total * 100) if total else 0
        print(f"     {tag:<{width}}  {bar:<8} {n}  ({pct:.1f}%)")
    print()
    print("   Two thirds of your edits are the same framework-signature false")
    print("   positive. The next prompt you ship for the bot names that")
    print("   pattern instead of guessing.")


def _task_to_pr(send):
    """Map task_id -> PR label, read from each task.create input."""
    mapping = {}
    for e in send("audit.read", workspace=WORKSPACE,
                  filter={"method": "task.create"})["entries"]:
        p = e["envelope"]["params"]
        pr = (p.get("input") or {}).get("pr")
        # task.create's own result carries the task_id; the audit entry keeps
        # the request params. Recover task_id from the result echoed on the
        # task's later events instead if absent here.
        tid = p.get("task_id")
        if tid and pr:
            mapping[tid] = pr
    if mapping:
        return mapping
    # Fallback: task.create does not echo task_id in params. Rebuild the map
    # from review.request entries, which carry both task_id and the artefact
    # (the artefact holds the PR label).
    for e in send("audit.read", workspace=WORKSPACE,
                  filter={"method": "review.request"})["entries"]:
        p = e["envelope"]["params"]
        tid = p.get("task_id")
        pr = (p.get("artefact") or {}).get("pr")
        if tid and pr:
            mapping[tid] = pr
    return mapping


if __name__ == "__main__":
    main()
