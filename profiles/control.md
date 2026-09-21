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

A snapshot MUST use the `Artefact` shape defined in
[`chap-task.schema.json`](../schemas/core/chap-task.schema.json): `id`,
`kind: "snapshot"`, `produced_by`, `produced_at`, `content_hash`, and inline
`content`. `content_hash` MUST be the `sha256:<hex>` digest of the JCS
canonical bytes of **content**, not of the whole artefact or just `state`.

The result contains `snapshot_artefact_id`, `audit_seq`, and `artefact`.
`content` contains `workspace`, `audit_seq`, `include`, `state`, and an optional
non-empty `label`. The two `audit_seq` values identify the number of entries
captured before the snapshot operation itself is appended: entries
`[0, audit_seq)`. The result's `snapshot_artefact_id` equals `artefact.id`.

Each `include` slice has exactly this projection in `content.state`:

| Slice | Captured fields | Rollback behavior |
|-------|-----------------|-------------------|
| `members` | `members`: list of `uri`, `type`, `role`, and optional non-empty `scopes`. | Restore role and scopes for members still present; do not recreate departed members. |
| `mode_ceiling` | `mode_ceiling`: the workspace value. | Restore the captured ceiling. |
| `open_tasks` | `open_tasks`: list of `id`, `kind`, `state`, and `assignee` for tasks not in `completed`, `declined`, `cancelled`, or `superseded`. | Informational only; tasks are not restored. |
| `policy` | `routing_policy_uri`, when present and non-empty. | Informational only. |
| `audit` | `audit_seq`. | Informational only; history is never truncated. |

Optional absent or empty fields and empty `members`/`open_tasks` collections
MUST be omitted. Numeric zero is not empty. The structural `state` object
remains present even when the selected slices have no values to expose.
Member capabilities, keys, joined timestamps, task inputs, outputs, histories,
and review bodies are not part of this projection. Full tasks remain in their
own audit entries rather than being duplicated in the snapshot artefact.
The `include` list retains the caller's selection order.
`include: []` and `what_to_restore: []` MUST be refused with `-32602`; omitting
`include` selects `members`, `open_tasks`, and `mode_ceiling`, while omitting
`what_to_restore` selects the snapshot's captured slices. Non-empty lists retain
partial selection.

Rollback MUST read the captured `artefact.content.state`; there is no separate
internal wire representation. Restoring mutable fields must not alias them
back into the saved snapshot. Snapshot content and its hash remain stable
after live state changes or library callers modify a returned response.

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

For example, a capture selecting only `mode_ceiling` returns the following
canonical response (deterministic fixture identifiers and timestamp):

```json
{
  "snapshot_artefact_id": "art_01HF7YAT010009WDVSQ5ZMMZ0N",
  "audit_seq": 3,
  "artefact": {
    "id": "art_01HF7YAT010009WDVSQ5ZMMZ0N",
    "kind": "snapshot",
    "produced_by": "human:reviewer@example.org",
    "produced_at": "2023-11-14T22:13:24.000Z",
    "content_hash": "sha256:03c0428e5b84736c4fbcb4274d10da851dfb1f57d87dfc927694b037c284d4a0",
    "content": {
      "workspace": "wsp_snapshot_conformance",
      "audit_seq": 3,
      "include": [
        "mode_ceiling"
      ],
      "state": {
        "mode_ceiling": "production"
      }
    }
  }
}
```

Shared [conformance vectors](../conformance/control-snapshot-vectors.md) fix
exact responses and hashes for each slice and the default projection.


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
reduces to "the caller has the admin role", protocol-level
enforcement is left to the deployment.

---

## 6. Error codes

| Code      | Meaning                                                  |
|-----------|----------------------------------------------------------|
| `-32060`  | Step-up authentication required (see `identity-oidc`).   |
| `-32061`  | The control operation is refused: the caller is not authorised, or the task is already settled (`completed`, `declined`, `cancelled`, `superseded`). |
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
