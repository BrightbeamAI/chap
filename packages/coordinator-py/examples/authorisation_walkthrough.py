#!/usr/bin/env python3
"""
Authorisation walkthrough: who may decide a review, and who may not.

Runs against an in-process Coordinator (no server needed):

    python examples/authorisation_walkthrough.py

It exercises both the allowed and the refused paths so the membership +
reviewer-set rules are visible end to end:

  ALLOWED
    1. approve   - the addressed reviewer approves a draft
    2. override  - the addressed reviewer edits, then accepts (the diff,
                   rationale, and tags become structured learning data)

  REFUSED (each rejected with NOT_AUTHORISED / -32011)
    3. a participant who never joined tries to decide
    4. a joined member who was not an addressed reviewer tries to decide

See SPECIFICATION.md S6.3.1 (actor membership) and profiles/review.md
S3.2 (reviewer-set eligibility).
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E


WS = "wsp_pr_reviews"
ALICE = "human:alice@example.org"   # the addressed reviewer
BOT = "agent:triage-bot"            # the drafting agent
BYSTANDER = "human:bob@example.org"  # a member, but not addressed
GHOST = "human:ghost@example.org"    # never joined


def banner(text: str) -> None:
    print("\n" + text)
    print("-" * len(text))


def main() -> None:
    coord = Coordinator(CoordinatorOptions(
        default_profiles=["core/1.0", "review/1.0"],
    ))

    n = {"i": 0}

    def call(method: str, expect_error: int | None = None, **params) -> dict:
        n["i"] += 1
        resp = coord.dispatch({
            "jsonrpc": "2.0", "id": f"c{n['i']}",
            "method": method, "params": params,
        })
        if "error" in resp:
            code = resp["error"]["code"]
            tag = "REFUSED " if expect_error is not None else "ERROR   "
            print(f"  {tag}{method}: [{code}] {resp['error']['message']}")
            if expect_error is not None and code != expect_error:
                raise SystemExit(
                    f"expected error {expect_error} from {method}, got {code}")
            if expect_error is None:
                raise SystemExit(f"unexpected error from {method}: {resp['error']}")
        else:
            if expect_error is not None:
                raise SystemExit(
                    f"expected {method} to be refused with {expect_error}, "
                    f"but it succeeded")
            print(f"  ok      {method}: {resp.get('result')}")
        return resp

    banner("Setup: a workspace, a human reviewer, a drafting agent, a task")
    call("workspace.create", workspace=WS, profiles=["core/1.0", "review/1.0"])
    call("participant.join", workspace=WS, **{"from": ALICE}, type="human", role="reviewer")
    call("participant.join", workspace=WS, **{"from": BOT}, type="agent", role="drafter")

    created = call("task.create", workspace=WS, **{"from": ALICE},
                   kind="code_review", assignee=BOT,
                   input={"pr_id": "PR-482"})
    task_id = created["result"]["task_id"]

    banner("The agent drafts and asks Alice to review")
    call("task.update", workspace=WS, **{"from": BOT}, task_id=task_id,
         state="in_progress")
    draft = {"verdict": "request_changes", "severity": "warning",
             "comment": "Refactor before merge."}
    call("task.complete", workspace=WS, **{"from": BOT}, task_id=task_id,
         output=draft, confidence=0.82)
    call("review.request", workspace=WS, **{"from": BOT}, task_id=task_id,
         to=[ALICE], artefact=draft, rule="any_one_approves")

    banner("REFUSED 1: a participant who never joined tries to decide")
    call("decide.approve", workspace=WS, **{"from": GHOST}, task_id=task_id,
         expect_error=E.NOT_AUTHORISED)

    banner("REFUSED 2: a joined member who was not asked to review tries to decide")
    call("participant.join", workspace=WS, **{"from": BYSTANDER},
         type="human", role="observer")
    call("decide.override", workspace=WS, **{"from": BYSTANDER}, task_id=task_id,
         diff=[{"op": "replace", "path": "/severity", "value": "info"}],
         rationale="not mine to judge", expect_error=E.NOT_AUTHORISED)

    banner("ALLOWED: the addressed reviewer overrides (edit, then accept)")
    call("decide.override", workspace=WS, **{"from": ALICE}, task_id=task_id,
         diff=[{"op": "replace", "path": "/severity", "value": "info"}],
         rationale="False positive: framework convention, not a bug.",
         tags=["false-positive", "framework-pattern"],
         intent_preserved=True)

    banner("The audit log: the override is now structured learning data")
    audit = call("audit.read", workspace=WS,
                 filter={"method": "decide.override", "task_id": task_id})
    entries = audit["result"]["entries"]
    assert len(entries) == 1, f"expected 1 override, got {len(entries)}"
    ov = entries[0]["envelope"]["params"]
    print(f"    reviewer:  {ov['from']}")
    print(f"    tags:      {ov['tags']}")
    print(f"    rationale: {ov['rationale']}")

    print("\nWalkthrough complete: 2 decisions refused, 1 override recorded.")


if __name__ == "__main__":
    main()
