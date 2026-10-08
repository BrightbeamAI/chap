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

The target MAY be a `group:` URI, and the first member to accept wins
(§5). No notification is delivered yet, so the proposal is not fanned
out to the group.

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

`accepted_task_ids: []` MUST be refused with `-32602`; omitting `accepted_task_ids` accepts all proposed tasks, and a non-empty list selects a subset.

On accept, the Coordinator atomically:

1. Updates each accepted task's `assignee` to the accepter.
2. Records the assignee change in the audit log.

Accepting makes the accepter the assignee, so an accepter paused under
`control/1.0` is refused with `-32063` ([`control.md`](./control.md) §2).
The handoff stays open, and the accepter can take it once resumed.

The profile also specifies a notification to interested participants.
It is not built yet, so no notification is delivered.

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

Only the named recipient's decline resolves a handoff. A decline from
anyone else is recorded and returns `state: "proposed"`, and the
handoff stays open. The original assignee stays the assignee and can
propose a fresh handoff to the suggested target.

---

## 5. Group handoffs

```json
{
  "to": "group:incident-on-call@example.org"
}
```

Any workspace member may accept a handoff addressed to a `group:` URI,
because the Coordinator does not model group membership. The first
accept reassigns the tasks, and a later accept is refused with
`-32051`. A decline by one member leaves the handoff open for the
others. This is how follow-the-sun coverage works without a human
dispatcher. No notification is delivered yet: the Coordinator neither
routes the proposal to the group nor tells the others when one member
accepts.

---

## 6. Error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32050`  | One or more task ids are not currently assigned to the proposer. |
| `-32051`  | Handoff has already been accepted/declined.      |
| `-32052`  | The recipient is not a workspace member, or the accepter is not the named recipient. |
| `-32063`  | The accepter is a paused participant.            |

---

## 7. Worked example

Full walk-through in [`../examples/07-handoff-shift-change.md`](../examples/07-handoff-shift-change.md).
