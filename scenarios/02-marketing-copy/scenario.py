#!/usr/bin/env python3
"""
Scenario 2: marketing copy with one drafter and one editor.

IN_PRACTICE.md S2. A two-person marketing function adds an agent (an
``agent:`` participant here) that turns a client brief into a first draft.
The editor signs off, edits, or sends back each draft before it ships, and
keeps making the same kinds of edits, softening corporate openers most of
all. Every decision is a CHAP envelope on a hash-linked audit chain, so the
"what do I keep fixing?" question becomes a query instead of a Friday
guess.

This uses CHAP core plus two profiles that ship with the reference
coordinator (review/1.0, audit-scitt/1.0). No framework, no network, and no
dependencies beyond the standard library.

Run it:

    # from a clone of the repo, nothing to install:
    python3 scenario.py

    # or, once chap-coordinator is on PyPI:
    pip install chap-coordinator && python3 scenario.py

It prints three things, in order:
  1. a check that the audit chain is intact, and that a tamper would be
     caught;
  2. a reconstruction of one past edit: exactly what the editor changed on
     the ACME brief, and why;
  3. the override learning report: the tag breakdown that points the next
     prompt revision at the opener problem.
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


WORKSPACE = "wsp_marketing"
EDITOR = "human:editor@studio.com"   # edits and approves; the only reviewer
DRAFTER = "agent:copybot@studio.com"  # turns each brief into a first draft
PROFILES = ["core/1.0", "review/1.0", "audit-scitt/1.0"]
GENESIS = "sha256:" + "0" * 64

# The generic first draft the agent tends to produce: a corporate opener and
# a passive body line. Each brief starts from this shape; the editor's edits
# are recorded as diffs against it.
def _draft(brief: str) -> dict:
    return {"brief": brief, "sections": [
        {"text": "Industry-leading solutions for forward-thinking teams."},
        {"text": "Our product is used by many companies to be enabled."},
    ]}


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
# Two months of briefs: a mix of ship-as-is, edit, and send-back.
# ---------------------------------------------------------------------------

WARM_OPENER = "We help teams ship faster. Here's how."
ACTIVE_BODY = "Hundreds of teams build on our product every day."
PLAIN_BODY = "Teams use our product to ship on time."

# (brief, decision, diff, rationale, tags). A representative slice; a real
# workspace would hold hundreds. opener-rewritten dominates on purpose.
REVIEWS = [
    ("acme-q3", "override",
     [{"op": "replace", "path": "/sections/0/text", "value": WARM_OPENER}],
     "Opener was generic corporate boilerplate.",
     ["opener-rewritten", "tone-corporate-to-warm"]),
    ("beacon-launch", "approve", None, "Clean draft; shipped as-is.", []),
    ("cobalt-email", "override",
     [{"op": "replace", "path": "/sections/1/text", "value": ACTIVE_BODY}],
     "Passive body line; made it active.",
     ["passive-to-active"]),
    ("delta-landing", "override",
     [{"op": "replace", "path": "/sections/0/text", "value": WARM_OPENER}],
     "Same corporate opener; rewrote it.",
     ["opener-rewritten"]),
    ("ember-ad", "override",
     [{"op": "replace", "path": "/sections/0/text", "value": WARM_OPENER}],
     "Opener leaned on a tired cliche; rewrote it.",
     ["opener-rewritten", "cliche-cut"]),
    ("flint-blog", "reject", None, "Off-brief; sent back for a fresh draft.",
     ["off-brief"]),
    ("gale-case-study", "override",
     [{"op": "replace", "path": "/sections/0/text", "value": WARM_OPENER}],
     "Corporate opener again.",
     ["opener-rewritten", "tone-corporate-to-warm"]),
    ("harbor-promo", "approve", None, "Good draft; minor polish only.", []),
    ("iris-newsletter", "override",
     [{"op": "replace", "path": "/sections/0/text", "value": WARM_OPENER}],
     "Opener boilerplate.",
     ["opener-rewritten"]),
    ("juno-web", "override",
     [{"op": "replace", "path": "/sections/1/text", "value": ACTIVE_BODY}],
     "Rewrote the passive construction.",
     ["passive-to-active"]),
    ("kilo-onepager", "override",
     [{"op": "replace", "path": "/sections/1/text", "value": PLAIN_BODY}],
     "Cut the best-in-class cliche from the body.",
     ["cliche-cut"]),
]


def build_history(send) -> None:
    send("workspace.create", workspace=WORKSPACE, profiles=PROFILES)
    send("participant.join", workspace=WORKSPACE, **{"from": EDITOR},
         type="human", role="reviewer")
    send("participant.join", workspace=WORKSPACE, **{"from": DRAFTER},
         type="agent", role="drafter")

    for brief, decision, diff, rationale, tags in REVIEWS:
        created = send("task.create", workspace=WORKSPACE, **{"from": EDITOR},
                       kind="copy_draft", assignee=DRAFTER, input={"brief": brief})
        task_id = created["task_id"]
        send("task.update", workspace=WORKSPACE, **{"from": DRAFTER},
             task_id=task_id, state="in_progress")

        artefact = _draft(brief)
        send("task.complete", workspace=WORKSPACE, **{"from": DRAFTER},
             task_id=task_id, output=artefact)
        send("review.request", workspace=WORKSPACE, **{"from": DRAFTER},
             task_id=task_id, to=[EDITOR], artefact=artefact)

        if decision == "approve":
            send("decide.approve", workspace=WORKSPACE, **{"from": EDITOR},
                 task_id=task_id, comment=rationale, tags=tags)
        elif decision == "reject":
            send("decide.reject", workspace=WORKSPACE, **{"from": EDITOR},
                 task_id=task_id, comment=rationale, tags=tags)
        else:
            send("decide.override", workspace=WORKSPACE, **{"from": EDITOR},
                 task_id=task_id, diff=diff, rationale=rationale, tags=tags,
                 intent_preserved=True)


# ---------------------------------------------------------------------------
# 1. Integrity: the chain is hash-linked, so it is tamper-evident.
# ---------------------------------------------------------------------------

def _verify(entries):
    """Re-walk the hash chain the way an auditor would. Each entry carries the
    prev_hash it was linked against; the next link is
    sha256(JCS(envelope) || prev_hash). If any envelope was altered after the
    fact, the recomputed links stop matching."""
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
            e["envelope"]["params"]["from"] = "human:someone-else@studio.com"
            break
    intact_after, broken_seq = _verify(forged)
    print(f"   If one decision were quietly edited: verifies = {intact_after}"
          f" (breaks at seq {broken_seq})")
    print("   -> the copy that ships was approved by a named editor, provably,")
    print("      and that record cannot be rewritten after the fact.")
    print()


# ---------------------------------------------------------------------------
# 2. Reconstruction: exactly what the editor changed on one brief, and why.
# ---------------------------------------------------------------------------

def print_reconstruction(send) -> None:
    overrides = send("audit.read", workspace=WORKSPACE,
                     filter={"method": "decide.override"})["entries"]
    task_to_brief = _task_to_brief(send)

    print("2. Two months later: what did the editor change on the ACME brief?")
    print("=" * 52)
    target = None
    for e in overrides:
        if task_to_brief.get(e["envelope"]["params"]["task_id"]) == "acme-q3":
            target = e
            break
    if target is None:
        target = overrides[0]
    p = target["envelope"]["params"]
    print(f"   editor:            {p['from']}")
    print(f"   intent preserved:  {p.get('intent_preserved')}")
    print(f"   edit (JSON Patch): {p['diff']}")
    print(f"   rationale:         {p['rationale']}")
    print(f"   tags:              {p.get('tags')}")
    print("   -> not 'the editor tweaked it', but the precise diff, the reason,")
    print("      and the category, recovered from the record.")
    print()


# ---------------------------------------------------------------------------
# 3. The learning report: the tag breakdown you feed back into the prompt.
# ---------------------------------------------------------------------------

def print_override_report(send) -> None:
    overrides = send("audit.read", workspace=WORKSPACE,
                     filter={"method": "decide.override"})["entries"]
    total = len(overrides)
    counts: Counter = Counter()
    for e in overrides:
        for tag in (e["envelope"]["params"].get("tags") or []):
            counts[tag] += 1

    print("3. Override Learning Report (wsp_marketing)")
    print("=" * 52)
    print(f"   Total overrides: {total}")
    print("   By tag:")
    width = max((len(t) for t in counts), default=0)
    for tag, n in counts.most_common():
        bar = "#" * n
        pct = (n / total * 100) if total else 0
        print(f"     {tag:<{width}}  {bar:<8} {n}  ({pct:.1f}%)")
    print()
    print("   The opener is where most of the editing goes. The next prompt")
    print("   revision bans those corporate opener patterns by name, and the")
    print("   team watches the rewrite rate drop instead of guessing.")


def _task_to_brief(send):
    """Map task_id -> brief label. task.create does not echo the task_id in
    its params, so rebuild the map from review.request entries, which carry
    both the task_id and the artefact (the artefact holds the brief)."""
    mapping = {}
    for e in send("audit.read", workspace=WORKSPACE,
                  filter={"method": "review.request"})["entries"]:
        p = e["envelope"]["params"]
        tid = p.get("task_id")
        brief = (p.get("artefact") or {}).get("brief")
        if tid and brief:
            mapping[tid] = brief
    return mapping


if __name__ == "__main__":
    main()
