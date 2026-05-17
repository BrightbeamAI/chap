# Example 04 — Abstain and escalate

**Scenario.** A junior reviewer is asked to approve a refund decision
that exceeds her authorisation limit. She abstains explicitly rather
than guessing, and the Coordinator escalates the task to her manager.
We also show the agent-initiated case where the agent itself abstains
because the request falls outside its competence envelope.

This example shows:

- `abstain.declare` from a human who declines to decide.
- `escalate.raise` driving the next assignment.
- `abstain.declare` from an agent (low confidence, out of scope).
- How the chain records the reason and preserves the original draft.

---

## 4.1 Setup

A draft refund of £450 has been produced and assigned for review:

```json
{
  "id": "tsk_01HZA2K3K3X8M2V4N6P8R0T5A",
  "kind": "refund_decision",
  "state": "review_requested",
  "mode": "production",
  "assignee": "human:alice@example.org",
  "delegator": "agent:triage-bot#v3.2",
  "input": {
    "ticket_id": "INC-48244",
    "customer_id": "CUST-7K2M9",
    "proposed_amount_gbp": 450.00,
    "reason": "Product arrived damaged; customer rejecting replacement."
  },
  "review": {
    "required": true,
    "reviewers": ["human:alice@example.org"],
    "rule": "any_one_approves"
  }
}
```

Alice's authorisation limit is £200. She *could* reject, but the
correct outcome is probably approval — just not by her.

---

## 4.2 Reviewer abstains

```json
{
  "hap": "0.1",
  "id": "01HZA2K3K3X8M2V4N6P8R0T5B",
  "ts": "2026-05-17T11:02:14.301Z",
  "workspace": "wsp_support_triage",
  "from": "human:alice@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "abstain.declare",
  "params": {
    "task_id": "tsk_01HZA2K3K3X8M2V4N6P8R0T5A",
    "artefact_id": "art_01HZA2K3K3X8M2V4N6P8R0T5C",
    "reason": "Refund amount (£450) exceeds my authorisation limit (£200).",
    "category": "out_of_authority",
    "suggested_escalation_target": "human:bob@example.org"
  },
  "evidence": { "prev_hash": "sha256:7485…9607", "sig": "ed25519:alice-2026-05-17:…" }
}
```

The Coordinator records the abstention as an artefact:

```json
{
  "id": "art_01HZA2K3K3X8M2V4N6P8R0T5D",
  "kind": "abstention",
  "produced_by": "human:alice@example.org",
  "produced_at": "2026-05-17T11:02:14.301Z",
  "task": "tsk_01HZA2K3K3X8M2V4N6P8R0T5A",
  "based_on": "art_01HZA2K3K3X8M2V4N6P8R0T5C",
  "content": {
    "reason": "Refund amount (£450) exceeds my authorisation limit (£200).",
    "category": "out_of_authority",
    "suggested_escalation_target": "human:bob@example.org"
  },
  "content_hash": "sha256:8596…0718"
}
```

The task transitions to `abstained` from Alice's perspective and is
queued for escalation.

---

## 4.3 Coordinator escalates

The workspace policy includes an `escalation_chain` for the
`refund_decision` task kind. The Coordinator follows it:

```json
{
  "hap": "0.1",
  "id": "01HZA2K3K3X8M2V4N6P8R0T5E",
  "ts": "2026-05-17T11:02:14.412Z",
  "workspace": "wsp_support_triage",
  "from": "service:coordinator@example.org",
  "to":   "human:bob@example.org",
  "type": "request",
  "method": "escalate.raise",
  "params": {
    "original_task_id": "tsk_01HZA2K3K3X8M2V4N6P8R0T5A",
    "abstention_artefact_id": "art_01HZA2K3K3X8M2V4N6P8R0T5D",
    "context": {
      "ticket_id": "INC-48244",
      "abstaining_reviewer": "human:alice@example.org",
      "abstention_reason": "Refund amount (£450) exceeds my authorisation limit (£200).",
      "original_draft_artefact_id": "art_01HZA2K3K3X8M2V4N6P8R0T5C"
    },
    "new_task": {
      "id": "tsk_01HZA2K3K3X8M2V4N6P8R0T5F",
      "kind": "refund_decision",
      "state": "created",
      "mode": "production",
      "assignee": "human:bob@example.org",
      "delegator": "service:coordinator@example.org",
      "input": {
        "ticket_id": "INC-48244",
        "customer_id": "CUST-7K2M9",
        "proposed_amount_gbp": 450.00,
        "reason": "Product arrived damaged; customer rejecting replacement.",
        "supersedes": "tsk_01HZA2K3K3X8M2V4N6P8R0T5A"
      },
      "constraints": { "deadline": "2026-05-17T18:00:00Z" },
      "review": {
        "required": true,
        "reviewers": ["human:bob@example.org"],
        "rule": "any_one_approves"
      }
    }
  },
  "evidence": { "prev_hash": "sha256:9607…1829", "sig": "ed25519:coord-2026-05:…" }
}
```

Bob accepts (`task.accept`), reads the original draft and Alice's
abstention reason, and proceeds to a decision. The chain now links:

```
tsk_…T5A (Alice's review)  →  art_…T5C (original draft)
                            →  art_…T5D (abstention)
                            →  tsk_…T5F (Bob's review, supersedes T5A)
```

Both tasks remain in the chain. The supersession is explicit; nothing
is hidden.

---

## 4.4 Agent-initiated abstention

The same protocol covers the agent declining to act. Consider an agent
asked to triage a case it does not understand:

```json
{
  "hap": "0.1",
  "id": "01HZA3L4K3X8M2V4N6P8R0T6A",
  "ts": "2026-05-17T11:30:00.100Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "abstain.declare",
  "params": {
    "task_id": "tsk_01HZA3L4K3X8M2V4N6P8R0T6B",
    "reason": "Customer message is in a language I'm not configured for (detected: Korean; my supported languages: en, fr, de, es).",
    "category": "out_of_scope",
    "confidence_in_abstention": 0.99,
    "suggested_escalation_target": "group:multilingual-support@example.org"
  },
  "evidence": { "prev_hash": "sha256:1829…2937", "sig": "ed25519:k-2026-05-17a:…" }
}
```

An agent that *cannot reliably do something* should abstain. This is
strictly better than producing a low-confidence guess: the abstention
is a typed signal that downstream systems can use to route, learn from,
and report on. The agent gets credit for knowing what it doesn't know.

The Coordinator follows the same escalation path — to the multilingual
support group rather than a specific human — and the work continues.

---

## 4.5 Auditing abstention rates

Because abstentions are first-class evidence entries, an auditor can ask:

```json
{
  "hap": "0.1",
  "id": "01HZB1AAAAAAAAAAAAAAAAAAA1",
  "ts": "2026-05-24T11:00:00.000Z",
  "workspace": "wsp_support_triage",
  "from": "human:auditor@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "audit.read",
  "params": {
    "filter": {
      "method": "abstain.declare",
      "ts_range": { "from": "2026-05-01T00:00:00Z", "to": "2026-05-31T23:59:59Z" }
    },
    "aggregate": {
      "group_by": ["from", "params.category"]
    }
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:…" }
}
```

The result tabulates abstentions per Participant and category — a
direct measure of where the workspace's competence boundaries lie.

---

## What this gives you

- **An explicit "I should not decide this" signal** — better than
  a bad approval, better than an unanswered queue item.
- **A reasoned escalation** with the original draft preserved.
- **Auditable confidence boundaries.** Abstention rates by category
  are a primary input for tuning agent scope and human authorisation
  matrices.

Move on to [`05-override-capture.md`](./05-override-capture.md) for
the case where the human approves but edits the draft.
