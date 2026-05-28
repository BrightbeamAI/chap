# Example 08: Multi-human deliberation

**Scenario.** Three engineers must decide together whether to ship a
hotfix that closes a low-severity bug at the cost of a small risk of
new behaviour in a high-traffic code path. Engineering lead, security,
and product weigh in. The decision rule is **weighted vote with
veto**, security can block, engineering and product carry weight.
The thread runs over the protocol from open to close.

This example shows:

- `deliberate.open` opening a thread with a defined rule and quorum.
- `deliberate.comment` notifications adding context.
- `deliberate.vote` requests carrying votes.
- `deliberate.close` computing the outcome.

Multi-human deliberation is the protocol's answer to *"three of us
need to agree on this."* It is intentionally separate from review: a
review approves a single artefact; a deliberation reaches a group
decision and may produce no artefact at all.

---

## 8.1 Opening the thread

The engineering lead opens the deliberation:

```json
{
  "chap": "0.2",
  "id": "01HZBC9P8K3X8M2V4N6P8R0TAA",
  "ts": "2026-05-17T15:10:01.001Z",
  "workspace": "wsp_release_decisions",
  "from": "human:lee-eng-lead@example.org",
  "to":   ["human:morgan-security@example.org", "human:sam-product@example.org"],
  "type": "request",
  "method": "deliberate.open",
  "params": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "question": "Ship hotfix v4.2.1 today? Closes #1187 (low-severity rendering bug) but touches the order-confirmation path.",
    "context": {
      "patch_summary": "Three-line fix in confirmation-template rendering. Adds a null-guard on display_name.",
      "test_summary": "Unit + integration green. Manual smoke pass on staging. Coverage of confirmation path: 84% line, 78% branch.",
      "risk_summary": "If something goes wrong here, customers may see broken order confirmations during checkout.",
      "alternatives": "Wait for the regular Tuesday release (4 days)."
    },
    "rule": "weighted_vote_with_veto:2.0",
    "weights": {
      "human:lee-eng-lead@example.org":    1.0,
      "human:morgan-security@example.org": 1.0,
      "human:sam-product@example.org":     1.0
    },
    "veto": {
      "human:morgan-security@example.org": true
    },
    "deadline": "2026-05-17T16:00:00Z"
  },
  "evidence": { "prev_hash": "sha256:3041…4152", "sig": "ed25519:lee-eng-lead-2026-05-17:…" }
}
```

The Coordinator:

- Validates the rule and weights against workspace policy.
- Notifies the named participants.
- Creates a thread that will close on `deliberate.close` or at the
  deadline.

---

## 8.2 Comments

The participants discuss in the thread. Comments are notifications;
they do not vote and do not affect the outcome.

```json
{
  "chap": "0.2",
  "id": "01HZBC9P8K3X8M2V4N6P8R0TAC",
  "ts": "2026-05-17T15:12:42.108Z",
  "workspace": "wsp_release_decisions",
  "from": "human:morgan-security@example.org",
  "to":   "workspace:wsp_release_decisions",
  "type": "notification",
  "method": "deliberate.comment",
  "params": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "text": "The patch itself looks safe. My concern is unrelated: we're in a quiet maintenance window per the change-management policy until Wednesday."
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:morgan-2026-05-17:…" }
}
```

```json
{
  "params": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "text": "Quiet window doesn't include explicit-allow-list hotfixes   bug #1187 was on the pre-approved list when we filed it last week."
  },
  "from": "human:lee-eng-lead@example.org"
}
```

```json
{
  "params": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "text": "From a CSAT angle, the bug is visible to about 200 customers/day on confirmation emails. Waiting four days is a real cost."
  },
  "from": "human:sam-product@example.org"
}
```

---

## 8.3 Votes

Each participant casts a vote. Votes are `request` messages so they
get an explicit accept-or-error from the Coordinator (a comment can
fail silently; a vote cannot).

```json
{
  "chap": "0.2",
  "id": "01HZBC9P8K3X8M2V4N6P8R0TAD",
  "ts": "2026-05-17T15:18:01.011Z",
  "workspace": "wsp_release_decisions",
  "from": "human:lee-eng-lead@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "deliberate.vote",
  "params": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "vote": "yea",
    "weight": 1.0,
    "comment": "Risk acceptable; pre-approved category."
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:lee-eng-lead-2026-05-17:…" }
}
```

```json
{
  "params": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "vote": "yea",
    "weight": 1.0,
    "comment": "Customer-impact reasoning persuades me."
  },
  "from": "human:sam-product@example.org"
}
```

Security votes yea but without the explicit veto:

```json
{
  "params": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "vote": "yea",
    "weight": 1.0,
    "comment": "Pre-approval check confirmed. Ship.",
    "veto_invoked": false
  },
  "from": "human:morgan-security@example.org"
}
```

---

## 8.4 Closing the thread

Once the participants have voted, the originator (or any admin)
closes the thread. The Coordinator computes the outcome:

```json
{
  "chap": "0.2",
  "id": "01HZBC9P8K3X8M2V4N6P8R0TAE",
  "ts": "2026-05-17T15:19:00.000Z",
  "workspace": "wsp_release_decisions",
  "from": "human:lee-eng-lead@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "deliberate.close",
  "params": { "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB" },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:lee-eng-lead-2026-05-17:…" }
}
```

The Coordinator responds:

```json
{
  "result": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "outcome": "approved",
    "rule": "weighted_vote_with_veto:2.0",
    "votes": [
      { "voter": "human:lee-eng-lead@example.org",    "vote": "yea", "weight": 1.0, "veto_invoked": false },
      { "voter": "human:sam-product@example.org",     "vote": "yea", "weight": 1.0, "veto_invoked": false },
      { "voter": "human:morgan-security@example.org", "vote": "yea", "weight": 1.0, "veto_invoked": false }
    ],
    "tally":  { "yea": 3.0, "nay": 0.0, "abstain": 0.0 },
    "vetoes": [],
    "outcome_artefact_id": "art_01HZBC9P8K3X8M2V4N6P8R0TAF"
  }
}
```

The outcome artefact:

```json
{
  "id": "art_01HZBC9P8K3X8M2V4N6P8R0TAF",
  "kind": "decision",
  "produced_by": "service:coordinator@example.org",
  "produced_at": "2026-05-17T15:19:00.090Z",
  "content": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "question": "Ship hotfix v4.2.1 today?",
    "outcome": "approved",
    "rule": "weighted_vote_with_veto:2.0",
    "tally": { "yea": 3.0, "nay": 0.0, "abstain": 0.0 },
    "voters": [
      "human:lee-eng-lead@example.org",
      "human:sam-product@example.org",
      "human:morgan-security@example.org"
    ]
  },
  "content_hash": "sha256:…"
}
```

The release agent (or a human, depending on workflow) reads the
outcome artefact and triggers the deploy. The deploy itself is a
separate task whose `delegator` field points back to the
deliberation's decision artefact, so the chain link from "we agreed"
to "we shipped" is one hop.

---

## 8.5 What if security had vetoed?

The same flow, but with:

```json
{
  "params": {
    "deliberation_id": "del_01HZBC9P8K3X8M2V4N6P8R0TAB",
    "vote": "nay",
    "weight": 1.0,
    "veto_invoked": true,
    "comment": "Pre-approval was for fixes inside the auth subsystem only. Confirmation-template fix is outside that scope."
  },
  "from": "human:morgan-security@example.org"
}
```

The rule `weighted_vote_with_veto:2.0` is short-circuited by the
veto. The Coordinator emits the close immediately:

```json
{
  "result": {
    "outcome": "rejected",
    "tally": { "yea": 2.0, "nay": 1.0 },
    "vetoes": [
      { "voter": "human:morgan-security@example.org", "comment": "Pre-approval was for fixes inside the auth subsystem only…" }
    ]
  }
}
```

The veto's reason is part of the chain. The team has both the answer
and the reason on the record.

---

## 8.6 Decision rules at a glance

| Rule                                       | When to use                                     |
|--------------------------------------------|-------------------------------------------------|
| `any_one_approves`                         | Routine, low-stakes, first ack wins.           |
| `all_approve`                              | Small, cross-functional decisions.              |
| `quorum:n`                                 | Boards or committees needing minimum attendance.|
| `weighted_vote:threshold`                  | When some voices weigh more (seniority, accountability). |
| `weighted_vote_with_veto:threshold`        | Senior + safety: typical for security/compliance gates. |

---

## What this gives you

- **A multi-human decision** captured as a typed object, question,
  rule, weights, comments, votes, outcome, reasons.
- **Audit-ready reasoning.** Why a hotfix shipped (or didn't) is
  permanent and queryable.
- **Vetoes preserve dissent.** A veto with reasoning is in the chain;
  the minority view is not erased.
- **Mechanism, not policy.** The rule set is small but covers the
  patterns most organisations use; new rules can be added in policy.

Move on to [`09-pause-resume-rollback.md`](./09-pause-resume-rollback.md)
for the operational control surface.
