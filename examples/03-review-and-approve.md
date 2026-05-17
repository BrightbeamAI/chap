# Example 03 — Review and approve (happy path)

**Scenario.** Continuing from [`02-task-delegation.md`](./02-task-delegation.md):
the agent has produced a draft response for ticket INC-48219. Alice
reviews it, finds it good, and approves. The Coordinator unifies the
CHAP and MCP evidence so the full chain — what Alice approved, plus
which tools were called to produce it — is one signed audit trail.

This example shows:

- `review.request` to open a review.
- `review.acknowledge` (optional) to signal the reviewer is engaged.
- `decide.approve` to close the review.
- How the unified audit query works.

---

## 3.1 Agent requests review

The agent's `task.complete` from §2.4 implicitly opens a review because
the task's `review.required` was `true`. Some implementations send
`review.request` explicitly for clarity; both shapes are valid.

Explicit form:

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T4A",
  "ts": "2026-05-17T09:14:27.105Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "human:alice@example.org",
  "type": "request",
  "method": "review.request",
  "params": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "artefact_id": "art_01HZ9YX7K3X8M2V4N6P8R0T3G",
    "rule": "any_one_approves",
    "reviewers": ["human:alice@example.org"],
    "deadline": "2026-05-17T09:30:00Z",
    "summary": "Drafted apology + carrier-tracking explanation; offered to open a missing-parcel case if not delivered by EOD tomorrow."
  },
  "evidence": { "prev_hash": "sha256:1829…30c3", "sig": "ed25519:…" }
}
```

The Coordinator pushes this to Alice's client. The client renders the
draft, the citations (with links to the MCP audit log entries), and
the agent's self-reported confidence.

---

## 3.2 Reviewer acknowledges

Alice's client signals engagement so the agent and the Coordinator can
update their UIs:

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T4B",
  "ts": "2026-05-17T09:14:39.221Z",
  "workspace": "wsp_support_triage",
  "from": "human:alice@example.org",
  "to":   "workspace:wsp_support_triage",
  "type": "notification",
  "method": "review.acknowledge",
  "params": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "artefact_id": "art_01HZ9YX7K3X8M2V4N6P8R0T3G"
  },
  "evidence": { "prev_hash": "sha256:30c3…41d4", "sig": "ed25519:…" }
}
```

This is informational; the protocol does not require it.

---

## 3.3 Reviewer approves

Alice has read the draft, spot-checked the carrier-tracking response,
and decides the reply is correct as written.

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T4C",
  "ts": "2026-05-17T09:15:11.014Z",
  "workspace": "wsp_support_triage",
  "from": "human:alice@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "decide.approve",
  "params": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "artefact_id": "art_01HZ9YX7K3X8M2V4N6P8R0T3G",
    "comment": "Looks good. Wording is on-brand and the carrier check is solid.",
    "tags": ["routine-approval"]
  },
  "evidence": { "prev_hash": "sha256:41d4…52e5", "sig": "ed25519:alice-2026-05-17:…" }
}
```

The Coordinator evaluates the rule: `any_one_approves` is satisfied
by Alice's single approval. The task transitions to `completed`. The
Coordinator notifies the agent and any other interested participants:

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T4D",
  "ts": "2026-05-17T09:15:11.099Z",
  "workspace": "wsp_support_triage",
  "from": "service:coordinator@example.org",
  "to":   ["agent:triage-bot#v3.2", "human:alice@example.org"],
  "type": "notification",
  "method": "notify.message",
  "params": {
    "kind": "task_completed",
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "final_state": "completed",
    "decision": "approve",
    "decision_artefact_id": "art_01HZ9YX7K3X8M2V4N6P8R0T4E"
  },
  "evidence": { "prev_hash": "sha256:52e5…63f6", "sig": "ed25519:coord-2026-05:…" }
}
```

A `decision` artefact (`art_01HZ9YX7K3X8M2V4N6P8R0T4E`) records the
approval itself:

```json
{
  "id": "art_01HZ9YX7K3X8M2V4N6P8R0T4E",
  "kind": "decision",
  "produced_by": "human:alice@example.org",
  "produced_at": "2026-05-17T09:15:11.014Z",
  "task": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
  "based_on": "art_01HZ9YX7K3X8M2V4N6P8R0T3G",
  "content": {
    "outcome": "approve",
    "comment": "Looks good. Wording is on-brand and the carrier check is solid.",
    "tags": ["routine-approval"]
  },
  "content_hash": "sha256:63f607182930415263748596071829304152637485960718293041526374a4b5"
}
```

---

## 3.4 Unified audit query

A week later, an auditor wants to know "exactly what produced this
reply to INC-48219?" They issue:

```json
{
  "chap": "0.1",
  "id": "01HZA1AAAAAAAAAAAAAAAAAAA1",
  "ts": "2026-05-24T11:00:00.000Z",
  "workspace": "wsp_support_triage",
  "from": "human:auditor@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "audit.read",
  "params": {
    "filter": {
      "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B"
    },
    "include_citations": true
  },
  "evidence": { "prev_hash": "sha256:abcd…ef01", "sig": "ed25519:auditor-…" }
}
```

The Coordinator returns the full chain segment for this task — eight
entries from `task.assign` to the `decide.approve` decision artefact
— **plus** the resolved citations (the MCP server's audit-log entries
that match the recorded hashes):

```json
{
  "result": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "evidence_range": [142, 149],
    "entries": [
      { "seq": 142, "method": "task.assign",   "from": "human:alice@example.org",   "ts": "2026-05-17T09:14:22.184Z" },
      { "seq": 143, "method": "task.accept",   "from": "agent:triage-bot#v3.2",     "ts": "2026-05-17T09:14:22.612Z" },
      { "seq": 144, "method": "task.start",    "from": "agent:triage-bot#v3.2",     "ts": "2026-05-17T09:14:22.790Z" },
      { "seq": 145, "method": "task.progress", "from": "agent:triage-bot#v3.2",     "ts": "2026-05-17T09:14:24.103Z" },
      { "seq": 146, "method": "task.complete", "from": "agent:triage-bot#v3.2",     "ts": "2026-05-17T09:14:27.012Z" },
      { "seq": 147, "method": "review.request","from": "agent:triage-bot#v3.2",     "ts": "2026-05-17T09:14:27.105Z" },
      { "seq": 148, "method": "review.acknowledge","from": "human:alice@example.org","ts": "2026-05-17T09:14:39.221Z" },
      { "seq": 149, "method": "decide.approve","from": "human:alice@example.org",   "ts": "2026-05-17T09:15:11.014Z" }
    ],
    "citations": [
      {
        "evidence_seq": 146,
        "artefact_id": "art_01HZ9YX7K3X8M2V4N6P8R0T3G",
        "kind": "mcp_tool_invocation",
        "server": "mcp+https://tools.example.org/orders",
        "tool": "lookup_order",
        "call_id": "call_01HZ9YX7K3X8M2V4N6P8R0T3H",
        "input_hash": "sha256:b2c3…2937",
        "output_hash": "sha256:d4e5…26a4",
        "external_audit_url": "https://tools.example.org/orders/audit/call_01HZ9YX7K3X8M2V4N6P8R0T3H",
        "external_audit_verified": true
      },
      {
        "evidence_seq": 146,
        "artefact_id": "art_01HZ9YX7K3X8M2V4N6P8R0T3G",
        "kind": "mcp_tool_invocation",
        "server": "mcp+https://tools.example.org/shipping",
        "tool": "carrier_tracking",
        "call_id": "call_01HZ9YX7K3X8M2V4N6P8R0T3J",
        "input_hash": "sha256:e5f6…a4b5",
        "output_hash": "sha256:f607…96c6",
        "external_audit_url": "https://tools.example.org/shipping/audit/call_01HZ9YX7K3X8M2V4N6P8R0T3J",
        "external_audit_verified": true
      }
    ]
  }
}
```

The `external_audit_verified` flag is set if the Coordinator's
side-channel check against the MCP server's published audit log
confirmed the hashes still match. (Implementations that do not
perform this side-channel check leave the field unset.)

---

## What this gives you

- **A complete approval record** — Alice's identity, the artefact
  she approved, when, with what comment.
- **A decision artefact** that downstream systems can use as a first-
  class object (e.g. for SLA reporting or compliance dashboards).
- **A unified audit** spanning CHAP and MCP. Every tool call that
  contributed to the approved output is identified, hash-verified,
  and linkable to its external audit log.

Move on to [`04-abstain-and-escalate.md`](./04-abstain-and-escalate.md)
for the unhappy-path versions.
