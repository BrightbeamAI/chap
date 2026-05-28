# Example 07: Shift-change handoff

**Scenario.** An incident-response workspace is staffed around the
clock. A senior responder, Daniel, is wrapping up his shift at 17:00
local. He has three open incidents in mid-investigation. Rather than
abandoning them or having his replacement, Priya, hunt through chat
scrollback, he uses `handoff.propose` to pass the open work over with
a structured summary.

This example shows:

- `handoff.propose` carrying summaries and pending decisions.
- `handoff.accept` from the receiving human.
- Mid-task assignee transition in the task descriptor.
- How the handoff appears in the chain.

This pattern generalises beyond shift changes: it covers escalations
between specialist teams, transfers between geographies for "follow
the sun" coverage, and graceful re-routing when a human goes offline.

---

## 7.1 The outgoing situation

Daniel has three tasks in `in_progress`:

```
tsk_…A1   Investigating elevated error rates in checkout service (45m in)
tsk_…A2   Drafting incident comms for status page (10m in, awaiting legal review)
tsk_…A3   Coordinating with vendor on third-party API outage (1h in)
```

He proposes a single handoff covering all three:

```json
{
  "chap": "0.2",
  "id": "01HZBA8O7K3X8M2V4N6P8R0T9A",
  "ts": "2026-05-17T16:50:14.001Z",
  "workspace": "wsp_incident_response",
  "from": "human:daniel@example.org",
  "to":   "human:priya@example.org",
  "type": "request",
  "method": "handoff.propose",
  "params": {
    "summary": "End-of-shift handoff. Three open incidents. Checkout error rate is trending down but vendor outage may still surprise us.",
    "tasks": [
      {
        "task_id": "tsk_01HZB8P7K3X8M2V4N6P8R0T9A1",
        "title": "Elevated checkout error rate",
        "status_summary": "Started 16:05. Rate peaked at 4.2% at 16:18, currently 1.1%. Root cause looks like the cache-warm step on the order-history service. Mitigation in progress.",
        "next_action": "Confirm cache-warm fix lands in 17:30 deploy window; verify error rate <0.5% sustained for 30 minutes.",
        "blockers": [],
        "watchlist": ["order-history-service p99 latency", "checkout-error-rate"]
      },
      {
        "task_id": "tsk_01HZB8P7K3X8M2V4N6P8R0T9A2",
        "title": "Status-page incident comms",
        "status_summary": "Draft posted at 16:40 in review.request to legal. Awaiting decision; deadline 17:15.",
        "next_action": "Pick up legal's decision; publish to status page on approve, revise on reject.",
        "blockers": ["legal review pending: tsk_01HZB8P7K3X8M2V4N6P8R0T9A2 review"]
      },
      {
        "task_id": "tsk_01HZB8P7K3X8M2V4N6P8R0T9A3",
        "title": "Third-party API outage coordination",
        "status_summary": "Vendor confirmed at 16:30   ETA for restoration 18:00 local. We are degrading gracefully; affected feature is exports. No customer-facing alarms.",
        "next_action": "Sit on it. If vendor misses 18:00, send the prepared user-comms message in art_01HZB8P7K3X8M2V4N6P8R0T9A4.",
        "blockers": ["vendor restoration"],
        "watchlist": ["vendor-api-status-page"]
      }
    ],
    "context_links": [
      "art_01HZB8P7K3X8M2V4N6P8R0T9A4",
      "art_01HZB8P7K3X8M2V4N6P8R0T9A5"
    ],
    "rationale": "End-of-shift; available again 08:00 tomorrow."
  },
  "evidence": { "prev_hash": "sha256:0718…1829", "sig": "ed25519:daniel-2026-05-17:…" }
}
```

The Coordinator routes the proposal to Priya. It does **not** transfer
ownership yet; ownership transfers only on acceptance.

---

## 7.2 Receiving human accepts

Priya skims the summaries, asks a single clarifying whisper (omitted
here for brevity), and accepts:

```json
{
  "chap": "0.2",
  "id": "01HZBA8O7K3X8M2V4N6P8R0T9B",
  "ts": "2026-05-17T16:54:02.221Z",
  "workspace": "wsp_incident_response",
  "from": "human:priya@example.org",
  "to":   "human:daniel@example.org",
  "type": "request",
  "method": "handoff.accept",
  "params": {
    "handoff_id": "01HZBA8O7K3X8M2V4N6P8R0T9A",
    "accepted_task_ids": [
      "tsk_01HZB8P7K3X8M2V4N6P8R0T9A1",
      "tsk_01HZB8P7K3X8M2V4N6P8R0T9A2",
      "tsk_01HZB8P7K3X8M2V4N6P8R0T9A3"
    ],
    "comment": "Have it. Will ping if vendor slips."
  },
  "evidence": { "prev_hash": "sha256:1829…2937", "sig": "ed25519:priya-2026-05-17:…" }
}
```

The Coordinator atomically updates each task's `assignee` field from
`human:daniel@example.org` to `human:priya@example.org` and emits a
notification to every interested participant:

```json
{
  "chap": "0.2",
  "id": "01HZBA8O7K3X8M2V4N6P8R0T9C",
  "ts": "2026-05-17T16:54:02.305Z",
  "workspace": "wsp_incident_response",
  "from": "service:coordinator@example.org",
  "to":   "workspace:wsp_incident_response",
  "type": "notification",
  "method": "notify.message",
  "params": {
    "kind": "handoff_complete",
    "handoff_id": "01HZBA8O7K3X8M2V4N6P8R0T9A",
    "transferred_tasks": [
      "tsk_01HZB8P7K3X8M2V4N6P8R0T9A1",
      "tsk_01HZB8P7K3X8M2V4N6P8R0T9A2",
      "tsk_01HZB8P7K3X8M2V4N6P8R0T9A3"
    ],
    "previous_assignee": "human:daniel@example.org",
    "new_assignee": "human:priya@example.org"
  },
  "evidence": { "prev_hash": "sha256:2937…3041", "sig": "ed25519:coord-2026-05:…" }
}
```

Three things happen in the chain as a result:

1. A `handoff.propose` entry (Daniel's signed summary).
2. A `handoff.accept` entry (Priya's signed acceptance).
3. A `notify.message` entry (the Coordinator's atomic transfer record).

If Daniel had taken any action on the tasks between proposing and
Priya accepting, those entries would interleave normally. Until Priya
accepts, Daniel remains the assignee and is on the hook.

---

## 7.3 What if Priya declines?

If Priya declines, the tasks stay with Daniel until he proposes a
different recipient or escalates:

```json
{
  "chap": "0.2",
  "id": "01HZBA8O7K3X8M2V4N6P8R0T9D",
  "ts": "2026-05-17T16:53:30.000Z",
  "workspace": "wsp_incident_response",
  "from": "human:priya@example.org",
  "to":   "human:daniel@example.org",
  "type": "request",
  "method": "handoff.decline",
  "params": {
    "handoff_id": "01HZBA8O7K3X8M2V4N6P8R0T9A",
    "reason": "I'm covering for Jamie and just took on the database-migration incident. Can you escalate to the on-call backup?",
    "suggested_target": "group:on-call-backup@example.org"
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:priya-2026-05-17:…" }
}
```

Declines are also evidence. Patterns of decline (which roles, what
reasons) are visible to operations leadership.

---

## 7.4 Group handoffs

Handoff targets can be groups. The Coordinator routes the proposal
to every member; the first to accept wins, and the others see a
`handoff_already_accepted` notification:

```json
{
  "params": {
    "summary": "End-of-shift handoff to whoever is up next.",
    "tasks": [ /* … */ ]
  },
  "to": "group:incident-on-call@example.org"
}
```

This is how follow-the-sun coverage works without a human dispatcher.

---

## What this gives you

- **No more shift-change scrollback hunts.** Status, blockers, and
  next actions arrive as structured fields.
- **Atomic assignee transfer** recorded in the chain. Auditors can
  see exactly when responsibility moved, by whose action.
- **Declines and lapses are visible.** Coverage gaps stop being
  invisible.
- **Group routing for on-call.** The protocol natively supports
  "whoever is up" rather than requiring a custom dispatcher.

Move on to [`08-multi-human-deliberation.md`](./08-multi-human-deliberation.md)
for multi-party decisions.
