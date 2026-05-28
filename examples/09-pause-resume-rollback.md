# Example 09: Pause, resume, snapshot, rollback

**Scenario.** A new version of the triage bot has been deployed to a
support workspace. Within 20 minutes, the override-analyser flags an
unusual spike in tone-related overrides. An on-call operator pauses
the agent's task acceptance, takes a snapshot of workspace state,
investigates, and ultimately rolls back the bot to the previous
version. The protocol records every step.

This example shows the **`control.*`** namespace end-to-end:

- `control.pause` halting new task acceptance.
- `control.snapshot` producing a point-in-time artefact.
- `control.supersede` replacing a problematic task.
- `control.rollback` reverting workspace state to a snapshot.
- `control.resume` returning to normal operation.

All control operations are **privileged**: they require step-up
authentication, are recorded as first-class evidence entries, and are
subject to the workspace policy's allow-list.

---

## 9.1 The trigger

The override-analyser service emits an alert as a notification:

```json
{
  "chap": "0.2",
  "id": "01HZBE0Q9K3X8M2V4N6P8R0TBA",
  "ts": "2026-05-17T16:18:30.000Z",
  "workspace": "wsp_support_triage",
  "from": "service:override-analyser@example.org",
  "to":   "group:on-call-ops@example.org",
  "type": "notification",
  "method": "notify.alert",
  "params": {
    "severity": "high",
    "title": "Override rate spike for agent:triage-bot#v3.3",
    "summary": "Tone-softened overrides are running at 67% over the last 30 minutes, vs. baseline 18% for the prior version v3.2. 41/61 tasks affected.",
    "links": [
      "art_01HZBE0Q9K3X8M2V4N6P8R0TBB"
    ]
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:override-analyser-2026-05:…" }
}
```

---

## 9.2 Operator pauses

The on-call operator, Jordan, recently re-authenticated. He sends:

```json
{
  "chap": "0.2",
  "id": "01HZBE0Q9K3X8M2V4N6P8R0TBC",
  "ts": "2026-05-17T16:19:42.014Z",
  "workspace": "wsp_support_triage",
  "from": "human:jordan-ops@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "control.pause",
  "params": {
    "scope": "participant",
    "participant_uri": "agent:triage-bot#v3.3",
    "reason": "Override-rate spike alert. Pausing new task acceptance pending investigation."
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:jordan-2026-05-17:…" }
}
```

`scope` can be `task`, `participant`, or `workspace`. Here, Jordan
pauses only the agent, the rest of the workspace (humans handling
their own queue, other agents) keeps running.

The Coordinator's response:

```json
{
  "result": {
    "scope": "participant",
    "participant_uri": "agent:triage-bot#v3.3",
    "paused_at": "2026-05-17T16:19:42.110Z",
    "in_flight_tasks": [
      "tsk_01HZBE0Q9K3X8M2V4N6P8R0TBD",
      "tsk_01HZBE0Q9K3X8M2V4N6P8R0TBE",
      "tsk_01HZBE0Q9K3X8M2V4N6P8R0TBF"
    ],
    "in_flight_policy": "allow_to_complete"
  }
}
```

The agent's in-flight tasks are allowed to complete; new `task.assign`
messages for the agent are rejected with error `-32500` (`policy_denied`)
carrying a `paused` reason. Coordinators MAY support an
`in_flight_policy` of `cancel` for emergencies.

---

## 9.3 Snapshot for investigation

Before changing anything, Jordan asks for a workspace snapshot:

```json
{
  "chap": "0.2",
  "id": "01HZBE0Q9K3X8M2V4N6P8R0TBG",
  "ts": "2026-05-17T16:20:10.000Z",
  "workspace": "wsp_support_triage",
  "from": "human:jordan-ops@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "control.snapshot",
  "params": {
    "label": "pre-rollback-investigation",
    "include": ["members", "open_tasks", "evidence_head", "policy_hash"]
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:jordan-2026-05-17:…" }
}
```

The Coordinator produces a snapshot artefact:

```json
{
  "id": "art_01HZBE0Q9K3X8M2V4N6P8R0TBH",
  "kind": "snapshot",
  "produced_by": "service:coordinator@example.org",
  "produced_at": "2026-05-17T16:20:10.150Z",
  "content": {
    "label": "pre-rollback-investigation",
    "ts": "2026-05-17T16:20:10.150Z",
    "evidence_head": "sha256:abc1…ef00",
    "evidence_count": 15842,
    "policy_hash": "sha256:abcd…1234",
    "members": [ /* current member list */ ],
    "open_tasks": [
      { "id": "tsk_…BD", "state": "in_progress", "assignee": "agent:triage-bot#v3.3" },
      { "id": "tsk_…BE", "state": "review_requested", "assignee": "human:alice@example.org" }
    ]
  },
  "content_hash": "sha256:…"
}
```

The snapshot is now an addressable rollback target.

---

## 9.4 Superseding a specific problematic task

Investigation finds one task with an override that's actively
shipping the wrong-tone response. Jordan replaces it with a fresh
task targeted at a different (older, known-good) agent version:

```json
{
  "chap": "0.2",
  "id": "01HZBE0Q9K3X8M2V4N6P8R0TBJ",
  "ts": "2026-05-17T16:24:00.001Z",
  "workspace": "wsp_support_triage",
  "from": "human:jordan-ops@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "control.supersede",
  "params": {
    "task_id": "tsk_01HZBE0Q9K3X8M2V4N6P8R0TBK",
    "successor_task": {
      "id": "tsk_01HZBE0Q9K3X8M2V4N6P8R0TBL",
      "kind": "draft_response",
      "state": "created",
      "mode": "production",
      "assignee": "agent:triage-bot#v3.2",
      "delegator": "human:jordan-ops@example.org",
      "input": { /* same input as the superseded task */ },
      "review": {
        "required": true,
        "reviewers": ["human:alice@example.org"],
        "rule": "any_one_approves"
      },
      "supersedes": "tsk_01HZBE0Q9K3X8M2V4N6P8R0TBK"
    },
    "reason": "v3.3 produced inappropriate tone; redoing on v3.2."
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:jordan-2026-05-17:…" }
}
```

The original task transitions to `superseded` (terminal); the
successor is in `created` and gets routed to v3.2.

---

## 9.5 Rolling back to the snapshot

After fifteen minutes of investigation, Jordan decides the right
move is to fully revert the workspace's mode policy to permit only
agent v3.2 for the time being. He uses `control.rollback`:

```json
{
  "chap": "0.2",
  "id": "01HZBE0Q9K3X8M2V4N6P8R0TBM",
  "ts": "2026-05-17T16:38:00.001Z",
  "workspace": "wsp_support_triage",
  "from": "human:jordan-ops@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "control.rollback",
  "params": {
    "to_snapshot_artefact_id": "art_01HZBE0Q9K3X8M2V4N6P8R0TBH",
    "what_to_restore": ["members", "policy_hash"],
    "reason": "Reverting active-agents list to exclude v3.3 pending issue investigation."
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:jordan-2026-05-17:…" }
}
```

**Important.** `control.rollback` does *not* truncate the evidence
chain. The chain is append-only by design. What rollback does is
write a new "rollback to snapshot X" evidence entry, then write new
entries that restore the snapshot's described state going forward.
The whole history, what happened during the bad window, the pause,
the snapshot, the supersession, the rollback, remains visible.

The Coordinator's response:

```json
{
  "result": {
    "from_state": {
      "evidence_count": 15901,
      "evidence_head": "sha256:c4d5…0011"
    },
    "to_snapshot": "art_01HZBE0Q9K3X8M2V4N6P8R0TBH",
    "restored_fields": ["members", "policy_hash"],
    "new_evidence_head": "sha256:c4d5…0099",
    "new_evidence_count": 15902
  }
}
```

The chain now contains one more entry, not fewer.

---

## 9.6 Resume

With the workspace back on the safe configuration, Jordan resumes:

```json
{
  "chap": "0.2",
  "id": "01HZBE0Q9K3X8M2V4N6P8R0TBN",
  "ts": "2026-05-17T16:40:11.000Z",
  "workspace": "wsp_support_triage",
  "from": "human:jordan-ops@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "control.resume",
  "params": {
    "scope": "workspace",
    "reason": "Rolled back agent allowlist; v3.3 isolated."
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:jordan-2026-05-17:…" }
}
```

The Coordinator un-pauses. Tasks resume. The agent v3.3 remains
disabled (because the member list was restored) until a separate
admin action re-enables it.

---

## 9.7 Auditing the incident

Three days later, a post-mortem owner queries the chain:

```json
{
  "method": "audit.read",
  "params": {
    "filter": {
      "method": ["control.pause", "control.resume", "control.snapshot",
                 "control.supersede", "control.rollback", "notify.alert"],
      "ts_range": { "from": "2026-05-17T16:00:00Z", "to": "2026-05-17T17:00:00Z" }
    }
  }
}
```

…returns the entire control sequence as one ordered list. The
incident timeline is the chain.

---

## What this gives you

- **A safe pause primitive**: the Coordinator stops accepting new
  work without affecting in-flight tasks.
- **Snapshots as first-class artefacts** that can be referenced by
  later operations.
- **Rollback that doesn't lie about history.** The bad window is
  permanently visible; rollback adds new entries to restore state,
  not remove old ones.
- **An auditable control plane.** Every pause, snapshot, supersede,
  and rollback is signed, hash-linked, and queryable.

Move on to [`10-end-to-end-workflow.md`](./10-end-to-end-workflow.md) to
see all of these stitched into one end-to-end trace.
