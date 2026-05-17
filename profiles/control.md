# Profile: `control`

**Profile id:** `control/1.0` · **Depends on:** Core

Operational control plane: pause, resume, supersede, snapshot,
rollback. Every operation is privileged and appended to the audit
log as a first-class entry.

---

## 1. New methods

| Method                       | Type    | Privileged | Summary                                       |
|------------------------------|---------|------------|-----------------------------------------------|
| `control.pause`              | request | yes        | Halt task acceptance at task/participant/workspace scope. |
| `control.resume`             | request | yes        | Reverse a pause.                              |
| `control.cancel`             | request | yes        | Terminal cancellation of a task.              |
| `control.supersede`          | request | yes        | Replace one task with another.                |
| `control.snapshot`           | request | yes        | Produce a point-in-time workspace artefact.   |
| `control.rollback`           | request | yes        | Restore workspace state from a snapshot. Appends, does not truncate. |
| `control.set_mode_ceiling`   | request | yes        | Change the workspace's mode ceiling (requires `modes`). |

---

## 2. Scope

`control.pause` and `control.resume` take a `scope`:

| Scope         | Effect                                                          |
|---------------|-----------------------------------------------------------------|
| `task`        | A specific task stops accepting updates.                        |
| `participant` | A specific participant stops being assigned new tasks; in-flight tasks complete by default. |
| `workspace`   | The whole workspace stops accepting new tasks.                  |

```json
{
  "method": "control.pause",
  "params": {
    "workspace":       "wsp_support_triage",
    "from":            "human:jordan-ops@example.org",
    "to":              "service:coordinator@example.org",
    "ts":              "2026-05-17T16:19:42Z",
    "scope":           "participant",
    "participant_uri": "agent:triage-bot#v3.3",
    "reason":          "Override-rate spike alert.",
    "in_flight_policy": "allow_to_complete"
  }
}
```

`in_flight_policy` ∈ `{ allow_to_complete, cancel }`.

---

## 3. Snapshot and rollback

`control.snapshot` captures workspace state at a point in time:

```json
{
  "method": "control.snapshot",
  "params": {
    "workspace": "wsp_support_triage",
    "from":      "human:jordan-ops@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T16:20:10Z",
    "label":     "pre-rollback-investigation",
    "include":   ["members", "open_tasks", "policy", "mode_ceiling"]
  }
}
```

Returns a snapshot artefact id that can be passed to `control.rollback`.

`control.rollback` **does not truncate the audit log.** It appends
a rollback entry and writes new entries that restore the snapshot's
recorded state going forward. The interim history remains visible.

```json
{
  "method": "control.rollback",
  "params": {
    "workspace":              "wsp_support_triage",
    "from":                   "human:jordan-ops@example.org",
    "to":                     "service:coordinator@example.org",
    "ts":                     "2026-05-17T16:38:00Z",
    "to_snapshot_artefact_id": "art_…",
    "what_to_restore":         ["members", "mode_ceiling"],
    "reason":                  "Reverting active-agents list to exclude v3.3."
  }
}
```

---

## 4. Supersede

Replace an in-flight or completed task with a successor:

```json
{
  "method": "control.supersede",
  "params": {
    "workspace": "wsp_support_triage",
    "from":      "human:jordan-ops@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T16:24:00Z",
    "task_id":   "tsk_OLD",
    "successor_task": {
      "kind": "draft_response",
      "assignee": "agent:triage-bot#v3.2",
      "input": { "...": "..." }
    },
    "reason": "v3.3 produced inappropriate tone; redoing on v3.2."
  }
}
```

The old task transitions to `superseded` (terminal); the successor
is created with `supersedes` linkage.

---

## 5. Privilege

Every `control.*` method is privileged. Implementations SHOULD
require step-up authentication (recent `auth_time`) via the
`identity-oidc` profile. Without `identity-oidc`, the requirement
reduces to "the caller has the admin role" — protocol-level
enforcement is left to the deployment.

---

## 6. Error codes

| Code      | Meaning                                                  |
|-----------|----------------------------------------------------------|
| `-32060`  | Step-up authentication required (see `identity-oidc`).   |
| `-32061`  | Caller is not authorised for control operations.         |
| `-32062`  | Snapshot artefact not found.                             |
| `-32063`  | Workspace is paused; this operation is blocked.          |

---

## 7. Composition notes

- **With `modes`:** `control.set_mode_ceiling` is the protocol-level
  way to promote/demote modes.
- **With `audit-scitt`:** every control operation is appended as a
  signed SCITT statement, providing cryptographic non-repudiation
  for operational changes.
- **With `identity-oidc`:** step-up auth is the recommended gate
  for privileged ops.

---

## 8. Worked example

Full walk-through in [`../examples/09-pause-resume-rollback.md`](../examples/09-pause-resume-rollback.md).
