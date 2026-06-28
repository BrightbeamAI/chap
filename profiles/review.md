# Profile: `review`

**Profile id:** `review/1.0` · **Depends on:** Core

The `review` profile adds the workflow most teams want when humans
oversee agent output: **request review**, **approve / reject /
override**, **abstain**, **escalate**. This is CHAP's highest-value
profile, it's where the structured-override learning data lives.

If you implement only one profile beyond Core, implement this one.

---

## 1. New methods

| Method               | Type            | Privileged? | Summary                                                |
|----------------------|-----------------|-------------|--------------------------------------------------------|
| `review.request`     | request         | no          | Ask one or more reviewers to evaluate an artefact.     |
| `review.acknowledge` | notification    | no          | Optional: reviewer signals they have begun review.     |
| `decide.approve`     | request         | no          | Approve a draft as-is.                                 |
| `decide.reject`      | request         | no          | Reject a draft with reasoning.                         |
| `decide.override`    | request         | no          | Approve a modified version; produces an override artefact. |
| `abstain.declare`    | request         | no          | Decline to decide; flags for escalation.               |
| `escalate.raise`     | request         | no          | Hand a task up the chain with context.                 |

---

## 2. New task states

The `review` profile adds these states to Core's `task` state machine:

```
created ──▶ in_progress ──▶ review_requested ──▶ completed (via decide.approve)
                                            ──▶ in_progress (via decide.reject)
                                            ──▶ abstained (via abstain.declare)
                                            ──▶ escalated (via escalate.raise)
                                            ──▶ completed (via decide.override, produces override artefact)
```

`abstained` is a terminal state for *this* task but typically
triggers `escalate.raise`, which creates a new task with the
original as `supersedes`.

---

## 3. Methods in detail

### 3.1 `review.request`

Open a review on a completed task. Issued either explicitly by the
task's completer, or implicitly when `task.complete` is called on a
task whose `review.required` was true.

```json
{
  "method": "review.request",
  "params": {
    "workspace":   "wsp_demo",
    "from":        "agent:triage-bot",
    "to":          ["human:alice@example.org"],
    "ts":          "2026-05-17T09:14:27.105Z",
    "task_id":     "tsk_…",
    "artefact":    { "kind": "draft", "content": { "...": "..." } },
    "rule":        "any_one_approves",
    "deadline":    "2026-05-17T09:30:00Z",
    "summary":     "Drafted apology + carrier-tracking explanation."
  }
}
```

Supported `rule` values: `any_one_approves`, `all_approve`.
The `deliberation` profile adds richer rules (`quorum:N`, weighted).

### 3.2 `decide.approve` · `decide.reject`

Straightforward. The reviewer's identity, comment, and tags are
preserved as a `decision` artefact.

**Eligibility.** The actor (`from`) of any review decision MUST be a
joined workspace member (Core §6.3.1) **and** MUST be one of the
reviewers the review was addressed to in `review.request`'s `to` set.
A Coordinator MUST reject a decision from a member who was not an
addressed reviewer with `-32011` (reviewer not authorised). This holds
for `decide.approve`, `decide.reject`, `decide.override`, and
`abstain.declare`. The `rule` field governs *how many* addressed
reviewers must decide for the review to terminate; the `to` set governs
*who is eligible* to decide at all.

A broadcast-scoped reviewer relaxes the second condition: if the `to`
set contains a `workspace:<id>` or `group:<id>` URI, any member (resp.
any member of that group) is an eligible reviewer, and only the
membership floor applies. If a review carries no recorded reviewer set,
the membership floor alone applies.

```json
{
  "method": "decide.approve",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T09:15:11Z",
    "task_id":   "tsk_…",
    "comment":   "Looks good.",
    "tags":      ["routine-approval"]
  }
}
```

```json
{
  "method": "decide.reject",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T09:15:11Z",
    "task_id":   "tsk_…",
    "comment":   "Tone is too apologetic for a non-delivery issue.",
    "tags":      ["tone-issue"],
    "request_revision": true
  }
}
```

If `request_revision` is true, the task transitions back to
`in_progress` rather than terminal `rejected`, giving the assignee
a chance to revise.

### 3.3 `decide.override`: the differentiator

The most valuable method in CHAP. The reviewer approves a *modified*
version of the draft, carrying a structured diff, rationale, and
tags. This is what turns human edits into learning data.

```json
{
  "method": "decide.override",
  "params": {
    "workspace":           "wsp_demo",
    "from":                "human:maya@example.org",
    "to":                  "service:coordinator@example.org",
    "ts":                  "2026-05-17T13:51:32Z",
    "task_id":             "tsk_…",
    "based_on_artefact":   { "...": "the original draft" },
    "logical_id":          "lgl_01HZ9YX1A2B3C4D5E6F7G8H9J0",
    "intent_preserved":    true,
    "diff": [
      {
        "op": "replace",
        "path": "/comments/1/text",
        "from": "This function is doing too many things…",
        "to":   "Consider splitting this function   not blocking for this PR."
      },
      {
        "op": "replace",
        "path": "/comments/1/severity",
        "from": "warning",
        "to":   "info"
      }
    ],
    "rationale": "Tone on the splitting comment was over-strong for a non-blocking suggestion.",
    "tags": ["tone-softened", "severity-downgraded"],
    "policy_refs": ["code-review-tone-guidelines-v2"]
  }
}
```

The `diff` MUST be a valid [RFC 6902 JSON Patch](https://datatracker.ietf.org/doc/html/rfc6902)
document. The Coordinator MUST be able to apply the patch to the
based-on artefact deterministically; if patch application fails, it
returns `-32602` with the error path.

When the based-on artefact carries a `logical_id`, the override
SHOULD carry the same `logical_id` and SHOULD set `intent_preserved`
, `true` if the override refines the *expression* of the same
underlying decision (as in the example above, where tone was
softened but the underlying review remains "approve with comments"),
`false` if the override substitutes a different decision (e.g. an
"approve" overridden to a "reject"). The field is informational;
CHAP does not constrain semantics. It exists because *"the human
edited the agent's draft"* and *"the human replaced the agent's
draft with a different decision"* are operationally different events
that produce identical envelope structures without it.

The resulting override is recorded as a typed audit entry. Any
downstream analytics, "which tags appear most often?", "which
agent has the highest override rate?", "which guidelines are most
frequently cited?", falls out of querying the audit log.

### 3.4 `abstain.declare`

Decline to decide. The reviewer signals "I'm not the right person
for this." This is strictly better than rejecting (which says the
work is wrong) or staying silent (which is invisible).

```json
{
  "method": "abstain.declare",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T11:02:14Z",
    "task_id":   "tsk_…",
    "reason":    "Refund amount (£450) exceeds my authorisation limit (£200).",
    "category":  "out_of_authority",
    "suggested_escalation_target": "human:bob@example.org"
  }
}
```

Standardised categories (extensible):

- `out_of_authority`: exceeds my limit / role
- `out_of_scope`: outside my competence
- `conflict_of_interest`
- `insufficient_context`
- `other`: free-text in `reason`

Abstention rates are a primary signal for tuning agent/role
boundaries. They're queryable as audit data.

### 3.5 `escalate.raise`

Hand a task up the chain. Typically issued by the Coordinator in
response to an abstention, but a human can issue it directly too.

```json
{
  "method": "escalate.raise",
  "params": {
    "workspace":            "wsp_demo",
    "from":                 "service:coordinator@example.org",
    "to":                   "human:bob@example.org",
    "ts":                   "2026-05-17T11:02:14Z",
    "original_task_id":     "tsk_…",
    "abstention_reason":    "Refund amount exceeds my authorisation limit.",
    "new_task": {
      "kind": "refund_decision",
      "assignee": "human:bob@example.org",
      "input": { "...": "...", "supersedes": "tsk_…" }
    }
  }
}
```

The Coordinator creates a new task whose `input.supersedes`
references the original, preserving the audit linkage.

---

## 4. Override-as-data: the unique value

Because every override carries a typed diff + rationale + tags, the
audit log becomes a structured tuning dataset for free. A weekly
aggregation:

```json
{
  "method": "audit.read",
  "params": {
    "workspace": "wsp_code_review",
    "filter": { "method": "decide.override", "ts_range": { "from": "2026-05-10", "to": "2026-05-17" } },
    "aggregate": { "group_by": ["params.tags", "params.from"] }
  }
}
```

…produces a tally like "67 overrides last week, 41 tagged
`tone-softened`, 16 tagged `false-positive-corrected`: by agent
version." That data didn't exist in the world before someone
captured it; the protocol makes capturing it the default.

This is the single most defensible reason to adopt CHAP. Every other
piece of the protocol is plumbing.

---

## 5. Error codes

| Code      | Meaning                                                |
|-----------|--------------------------------------------------------|
| `-32010`  | Task is not in a reviewable state.                     |
| `-32011`  | Actor is not authorised: not a workspace member, or a member who was not an addressed reviewer for this task. |
| `-32012`  | JSON Patch application failed (with path in `data`).   |
| `-32013`  | Review deadline has lapsed.                            |

---

## 6. Worked example

Full end-to-end walk-through in [`../examples/05-override-capture.md`](../examples/05-override-capture.md).

---

## 7. Composition notes

- **With `modes`:** override capture is most informative in `trial`
  mode, where every output is reviewed. The override rate is a
  primary promotion-to-production criterion.
- **With `deliberation`:** when a single reviewer's override would
  invoke a goodwill credit beyond their authority, the workflow can
  spawn a deliberation rather than a simple escalation.
- **With MCP citations:** override patterns can be correlated with
  the MCP tools the original draft used, "drafts that called the
  knowledge-base tool have a higher override rate; tune the
  knowledge-base agent first."
