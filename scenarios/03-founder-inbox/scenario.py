#!/usr/bin/env python3
"""
Scenario 3: the indie founder, the inbox, and the angry customer.

IN_PRACTICE.md S3. A solo SaaS founder puts a triage agent (an ``agent:``
participant here) in front of the support inbox: it drafts a reply, and the
founder approves, edits, or escalates each one. Six weeks in a customer
files a chargeback claiming the bot quoted the wrong refund policy. The
provider logs have expired and the keys have rotated, but every ticket ran
through a CHAP coordinator, so one query reconstructs the whole story, and a
scan of the same chain shows the founder approved the same wrong policy on
seven earlier tickets.

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
  2. the chargeback query: one ticket's full history, reconstructed in
     order from the chain (draft -> decision -> outbound), plus the ticket
     that was escalated into a new task;
  3. the pattern scan: the seven earlier tickets where the bot cited the
     same wrong policy, the moment the founder fixes retrieval.
"""
from __future__ import annotations

import copy
import sys
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


WORKSPACE = "wsp_support"
FOUNDER = "human:you@saas.com"        # approves, edits, or escalates; the reviewer
TRIAGE = "agent:triage@saas.com"      # drafts the first reply to each ticket
SPECIALIST = "human:specialist@saas.com"  # takes the tickets the founder escalates
PROFILES = ["core/1.0", "review/1.0", "audit-scitt/1.0"]
GENESIS = "sha256:" + "0" * 64

WRONG_POLICY = "refund-30d"   # what the bot's retrieval kept quoting
RIGHT_POLICY = "refund-14d"   # the actual policy, once retrieval is fixed
CHARGEBACK = "8821"           # the ticket the customer disputes


def _draft(ticket: str, reply: str, policy: str) -> dict:
    # policy_cited is content the bot put in its draft, deliberately kept
    # distinct from the protocol's policy_refs (the governing policy of a
    # task): the whole point of the scenario is that this citation is wrong.
    return {"ticket": ticket, "reply": reply, "policy_cited": policy}


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

    ctx = build_history(send)

    entries = send("audit.read", workspace=WORKSPACE)["entries"]
    print_integrity(entries)
    print_reconstruction(send, ctx)
    print_pattern_scan(send)


# ---------------------------------------------------------------------------
# Six weeks of tickets: mostly approved, a couple edited, one escalated.
# Seven of them quote the wrong refund policy before retrieval is fixed.
# ---------------------------------------------------------------------------

# (ticket, decision, policy, note, diff). The chargeback ticket and six others
# carry the wrong policy; one ticket is escalated; the last, after the fix,
# quotes the right one.
TICKETS = [
    ("8815", "approve",  WRONG_POLICY, "Standard refund question; looked fine.", None),
    ("8816", "approve",  WRONG_POLICY, "Refund window question; approved.", None),
    ("8817", "override", WRONG_POLICY, "Softened the tone before sending.",
     [{"op": "replace", "path": "/reply",
       "value": "Sorry for the hassle -- here's how refunds work."}]),
    ("8818", "approve",  WRONG_POLICY, "Another refund query; approved.", None),
    ("8819", "approve",  WRONG_POLICY, "Refund timing; approved.", None),
    ("8820", "override", WRONG_POLICY, "Added the account link.",
     [{"op": "replace", "path": "/reply",
       "value": "You can request a refund from your account settings."}]),
    (CHARGEBACK, "approve", WRONG_POLICY,
     "Refund declined per policy; approved and sent.", None),
    ("8822", "escalate", "billing-dispute",
     "Duplicate charge across two plans -- beyond a template reply.", None),
    ("8830", "approve",  RIGHT_POLICY,
     "Retrieval fixed; the draft now cites the real policy.", None),
]


def build_history(send) -> dict:
    send("workspace.create", workspace=WORKSPACE, profiles=PROFILES)
    for uri, type_, role in (
        (FOUNDER, "human", "reviewer"),
        (TRIAGE, "agent", "drafter"),
        (SPECIALIST, "human", "specialist"),
    ):
        send("participant.join", workspace=WORKSPACE, **{"from": uri},
             type=type_, role=role)

    ticket_to_task: dict = {}
    escalation: dict = {}

    for ticket, decision, policy, note, diff in TICKETS:
        created = send("task.create", workspace=WORKSPACE, **{"from": FOUNDER},
                       kind="support_reply", assignee=TRIAGE, input={"ticket": ticket})
        task_id = created["task_id"]
        ticket_to_task[ticket] = task_id
        send("task.update", workspace=WORKSPACE, **{"from": TRIAGE},
             task_id=task_id, state="in_progress")

        artefact = _draft(ticket, f"Reply for ticket {ticket}.", policy)
        send("task.complete", workspace=WORKSPACE, **{"from": TRIAGE},
             task_id=task_id, output=artefact)
        send("review.request", workspace=WORKSPACE, **{"from": TRIAGE},
             task_id=task_id, to=[FOUNDER], artefact=artefact)

        if decision == "approve":
            send("decide.approve", workspace=WORKSPACE, **{"from": FOUNDER},
                 task_id=task_id, comment=note)
        elif decision == "override":
            send("decide.override", workspace=WORKSPACE, **{"from": FOUNDER},
                 task_id=task_id, diff=diff, rationale=note, intent_preserved=True)
        else:  # escalate: hands the ticket up, opening a new task for the specialist
            result = send("escalate.raise", workspace=WORKSPACE, **{"from": FOUNDER},
                          original_task_id=task_id,
                          new_task={"assignee": SPECIALIST, "kind": "support_reply",
                                    "input": {"ticket": ticket, "reason": note}})
            escalation = {"ticket": ticket, "from_task": task_id,
                          "new_task": result["new_task_id"]}

    return {"ticket_to_task": ticket_to_task, "escalation": escalation}


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

    forged = copy.deepcopy(entries)
    for e in forged:
        if e["envelope"]["method"] == "decide.approve":
            e["envelope"]["params"]["from"] = "human:not-you@saas.com"
            break
    intact_after, broken_seq = _verify(forged)
    print(f"   If one approval were quietly reattributed: verifies = {intact_after}"
          f" (breaks at seq {broken_seq})")
    print("   -> when the chargeback lands, the draft, your decision, and the")
    print("      outbound are provable, not a story about expired logs.")
    print()


# ---------------------------------------------------------------------------
# 2. The chargeback query: one ticket's full history, in order.
# ---------------------------------------------------------------------------

def print_reconstruction(send, ctx) -> None:
    task_id = ctx["ticket_to_task"][CHARGEBACK]
    # Select every entry for this ticket. task.create carries the ticket in
    # its input (not a task_id, which the coordinator only returns in the
    # result), so match it separately; everything else carries task_id.
    story = []
    for e in send("audit.read", workspace=WORKSPACE)["entries"]:
        p = e["envelope"].get("params", {})
        if p.get("task_id") == task_id or (
            e["envelope"]["method"] == "task.create"
            and (p.get("input") or {}).get("ticket") == CHARGEBACK
        ):
            story.append(e)

    print(f"2. The chargeback: reconstruct ticket {CHARGEBACK} from the chain")
    print("=" * 52)
    for e in story:
        env = e["envelope"]
        p = env.get("params", {})
        detail = {
            "task.create":    "ticket opened, drafted by the triage agent",
            "task.update":    "agent drafting",
            "task.complete":  f"draft cites {(p.get('output') or {}).get('policy_cited')}",
            "review.request": "sent to the founder",
            "decide.approve": f"approved by {p.get('from')}",
        }.get(env["method"], "")
        print(f"   {env['method']:<16} {detail}")
    esc = ctx["escalation"]
    print(f"   (separately, ticket {esc['ticket']} was escalated -> new task"
          f" {esc['new_task']} for {SPECIALIST})")
    print("   -> the whole story in order, months after the provider logs expired.")
    print()


# ---------------------------------------------------------------------------
# 3. The pattern scan: how many tickets quoted the same wrong policy.
# ---------------------------------------------------------------------------

def print_pattern_scan(send) -> None:
    completed = send("audit.read", workspace=WORKSPACE,
                     filter={"method": "task.complete"})["entries"]
    wrong = []
    for e in completed:
        out = e["envelope"]["params"].get("output") or {}
        if out.get("policy_cited") == WRONG_POLICY:
            wrong.append(out["ticket"])

    print("3. Pattern scan: which tickets cited the wrong policy?")
    print("=" * 52)
    print(f"   Drafts citing '{WRONG_POLICY}' (the wrong policy): {len(wrong)}")
    print(f"   Tickets: {', '.join(wrong)}")
    print(f"   -> not one bad ticket but {len(wrong)}. You fix the agent's")
    print("      retrieval to consult the real policy; the next ticket cites")
    print(f"      '{RIGHT_POLICY}' and is correct.")


if __name__ == "__main__":
    main()
