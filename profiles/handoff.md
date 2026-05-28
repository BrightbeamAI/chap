# Profile: `handoff`

**Profile id:** `handoff/1.0` · **Depends on:** Core

Transfer in-progress work between participants, shift changes,
escalations, follow-the-sun coverage, graceful re-routing when a
human goes offline.

---

## 1. New methods

| Method            | Type    | Summary                                          |
|-------------------|---------|--------------------------------------------------|
| `handoff.propose` | request | Propose transferring one or more tasks.          |
| `handoff.accept`  | request | Accept a handoff. Atomically reassigns tasks.    |
| `handoff.decline` | request | Decline; suggest an alternative target.          |

---

## 2. `handoff.propose`

```json
{
  "method": "handoff.propose",
  "params": {
    "workspace":   "wsp_incident_response",
    "from":        "human:daniel@example.org",
    "to":          "human:priya@example.org",
    "ts":          "2026-05-17T16:50:14Z",
    "handoff_id":  "01HZ…",
    "summary":     "End-of-shift handoff. Three open incidents.",
    "tasks": [
      {
        "task_id": "tsk_…A1",
        "title":   "Elevated checkout error rate",
        "status_summary": "Rate down to 1.1%; cache-warm fix queued for 17:30 deploy.",
        "next_action":    "Confirm fix lands; verify <0.5% for 30 minutes.",
        "blockers": []
      },
      { "task_id": "tsk_…A2", "title": "Status-page incident comms", "status_summary": "Awaiting legal review.", "next_action": "Publish on approval." }
    ],
    "context_links": ["art_…1", "art_…2"]
  }
}
```

Ownership transfer is **not** atomic on propose. The proposer
remains the assignee until the recipient accepts.

The target MAY be a group; in that case the proposal is fanned out
and the first to accept wins.

---

## 3. `handoff.accept`

```json
{
  "method": "handoff.accept",
  "params": {
    "workspace":         "wsp_incident_response",
    "from":              "human:priya@example.org",
    "to":                "human:daniel@example.org",
    "ts":                "2026-05-17T16:54:02Z",
    "handoff_id":        "01HZ…",
    "accepted_task_ids": ["tsk_…A1", "tsk_…A2"],
    "comment":           "Have it. Will ping if vendor slips."
  }
}
```

On accept, the Coordinator atomically:

1. Updates each accepted task's `assignee` to the accepter.
2. Emits a notification to interested participants.
3. Records the assignee change in the audit log.

---

## 4. `handoff.decline`

```json
{
  "method": "handoff.decline",
  "params": {
    "workspace":        "wsp_incident_response",
    "from":             "human:priya@example.org",
    "to":               "human:daniel@example.org",
    "ts":               "2026-05-17T16:53:30Z",
    "handoff_id":       "01HZ…",
    "reason":           "Covering for Jamie; took on the database-migration incident.",
    "suggested_target": "group:on-call-backup@example.org"
  }
}
```

The original assignee stays the assignee. They can propose a fresh
handoff to the suggested target.

---

## 5. Group handoffs

```json
{
  "to": "group:incident-on-call@example.org"
}
```

The Coordinator routes the proposal to every group member; first
accepter wins, others receive a `handoff_already_accepted`
notification. This is how follow-the-sun coverage works without a
human dispatcher.

---

## 6. Error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32050`  | One or more task ids are not currently assigned to the proposer. |
| `-32051`  | Handoff has already been accepted/declined.      |
| `-32052`  | Recipient is not a workspace member.             |

---

## 7. Worked example

Full walk-through in [`../examples/07-handoff-shift-change.md`](../examples/07-handoff-shift-change.md).
