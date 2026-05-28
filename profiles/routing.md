# Profile: `routing`

**Profile id:** `routing/1.0` · **Depends on:** Core · **Composes with:** `review/1.0`, `modes/1.0`

Decide *how* a task gets handled, who picks it up, how deep the
review is, when to auto-escalate, based on the runtime signals
already carried in `routing_hints` on tasks and artefacts.

This profile adds three decision methods. It does not invent new
signals; it consumes the ones Core already defines and writes
back the decisions it makes as auditable events.

---

## 1. The split: signals vs decisions

Core carries the signals. This profile interprets them.

| Layer | Belongs in | Why |
|-------|-----------|-----|
| `criticality`, `deadline`, `max_cost_usd`, `risk_tier` on a task | Core (`Task.routing_hints`) | Any intermediary must forward and sign them. |
| `confidence`, `model_id`, `cost_consumed_usd`, `latency_ms` on an artefact | Core (`Artefact.routing_hints`) | Same reason, they need to survive un-routing-aware nodes. |
| "If criticality is `high` and confidence < 0.7, escalate." | This profile | A policy. Not every workspace shares it. |
| "Pick model A for criticality `low`, model B for `high`." | This profile | A routing rule. Operator-specific. |

CHAP's discipline: **the protocol carries the evidence; the
operator runs the policy.** This profile gives the policy a wire
format so its decisions become evidence too.

---

## 2. New methods

| Method            | Type    | Summary                                          |
|-------------------|---------|--------------------------------------------------|
| `task.route`      | request | Pick an assignee for a task from candidates, given hints. |
| `review.depth`    | request | Decide review depth for an artefact: skip, spot-check, full. |
| `escalate.auto`   | request | Evaluate auto-escalation rules against an artefact. |

Each method records its decision (and the hints consulted) as an
artefact in the evidence chain. A consumer reading the audit log
later can reconstruct *why* a routing choice happened.

---

## 3. `task.route`

Pick one assignee from a list of candidates, given the task and
its routing hints. Returns the selected URI and a structured
rationale.

```json
{
  "method": "task.route",
  "params": {
    "workspace":   "wsp_support_triage",
    "task_id":     "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "candidates": [
      "agent:fast-draft-v3@example.org",
      "agent:careful-draft-v2@example.org",
      "human:senior-pool@example.org"
    ],
    "ts": "2026-05-17T09:14:02Z"
  }
}
```

Response:

```json
{
  "result": {
    "selected": "agent:careful-draft-v2@example.org",
    "decision_artefact": "art_01HZ9YX7K3X8M2V4N6P8R0T3C",
    "rationale": {
      "policy_id":     "routing-policy-v4",
      "hints_used":    ["criticality", "max_cost_usd"],
      "summary":       "criticality=high routed to careful tier; max_cost_usd=$50 ruled out human-pool.",
      "alternatives_considered": [
        { "candidate": "agent:fast-draft-v3@example.org", "reason_excluded": "criticality=high not in fast-draft policy" },
        { "candidate": "human:senior-pool@example.org", "reason_excluded": "max_cost_usd exceeded by human-pool tariff" }
      ]
    }
  }
}
```

The `decision_artefact` is a new artefact of kind `route_decision`
that captures the inputs (the task's hints), the policy id, and the
selected assignee. It's signed into the evidence chain like any
other artefact.

After `task.route` succeeds, the Coordinator MUST update the task's
`assignee` to match `selected` and dispatch the task normally.

### `task.route` error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32510`  | `no_eligible_assignee`: no candidate satisfies the routing policy for this task's hints. |
| `-32511`  | `routing_policy_violation`: the requested route violates workspace `routing_policy_uri`. |
| `-32513`  | `candidates_empty`: `candidates` array was empty. |

---

## 4. `review.depth`

Given an artefact and its hints, decide whether to skip review,
spot-check, or do a full review. Returns a depth tier with rationale.

```json
{
  "method": "review.depth",
  "params": {
    "workspace":      "wsp_support_triage",
    "artefact_id":    "art_01HZ9YX7K3X8M2V4N6P8R0T3D",
    "task_id":        "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "ts":             "2026-05-17T09:14:48Z"
  }
}
```

Response:

```json
{
  "result": {
    "depth":               "spot_check",
    "decision_artefact":   "art_01HZ9YX7K3X8M2V4N6P8R0T3E",
    "sampling_probability": 0.10,
    "rationale": {
      "policy_id":  "review-depth-v2",
      "hints_used": ["criticality", "confidence", "model_id"],
      "summary":    "criticality=low + confidence>0.85: sampled at 10%."
    }
  }
}
```

Defined depth tiers (clients MUST accept these; profiles MAY add more):

| Tier         | Meaning                                                  |
|--------------|----------------------------------------------------------|
| `skip`       | No review required. Artefact is released immediately.    |
| `spot_check` | Random sampling. `sampling_probability` is in [0, 1]. The Coordinator MUST honour it. |
| `full`       | Every artefact reviewed.                                 |
| `escalated`  | Reserved; the depth-decider has invoked `escalate.auto`. |

If the workspace also runs `review/1.0`, the depth decision feeds
the `review.required` boolean and the reviewer set. If only Core
review is in use, the depth is informative only.

### `review.depth` error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32514`  | `depth_not_applicable`: the artefact's kind is not subject to review (e.g. `route_decision`). |
| `-32515`  | `policy_unreachable`: the routing-policy resource referenced is not loadable. |

---

## 5. `escalate.auto`

Evaluate the workspace's auto-escalation rules against an artefact.
Returns whether to escalate and to whom. The triggering rule is
recorded in the evidence chain.

```json
{
  "method": "escalate.auto",
  "params": {
    "workspace":   "wsp_support_triage",
    "artefact_id": "art_01HZ9YX7K3X8M2V4N6P8R0T3F",
    "task_id":     "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "ts":          "2026-05-17T09:15:30Z"
  }
}
```

Response when an escalation fires:

```json
{
  "result": {
    "escalate":          true,
    "to":                "group:senior-reviewers@example.org",
    "decision_artefact": "art_01HZ9YX7K3X8M2V4N6P8R0T3G",
    "triggered_rule": {
      "rule_id":   "auto-esc-3",
      "summary":   "criticality=critical AND confidence<0.6 → senior pool",
      "hints_used": ["criticality", "confidence"]
    }
  }
}
```

Response when no escalation fires:

```json
{
  "result": {
    "escalate": false,
    "decision_artefact": "art_01HZ9YX7K3X8M2V4N6P8R0T3H"
  }
}
```

An auto-escalation results in a new task (via the existing escalate
flow from `review/1.0`) addressed to `to`. The original task moves
to `escalated`.

### `escalate.auto` error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32512`  | `auto_escalation_triggered`: informational; emitted on a `decide.override` or `decide.reject` notification when an auto-rule also fired. |
| `-32516`  | `escalation_target_unavailable`: the rule-selected target is not a workspace member or group. |

---

## 6. New artefact kind: `route_decision`

The decision artefact recorded by each routing method. Standard
structure:

```json
{
  "id":          "art_01HZ9YX7K3X8M2V4N6P8R0T3E",
  "kind":        "route_decision",
  "produced_by": "service:coord@example.org",
  "produced_at": "2026-05-17T09:14:48Z",
  "task":        "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
  "content": {
    "decision_type": "review.depth",
    "outcome":       "spot_check",
    "policy_id":     "review-depth-v2",
    "hints_observed": {
      "criticality": "low",
      "confidence":  0.91,
      "model_id":    "draft-bot:2026-05"
    },
    "rationale": "criticality=low + confidence>0.85: sampled at 10%."
  },
  "content_hash": "sha256:…"
}
```

`decision_type` is one of `task.route`, `review.depth`, `escalate.auto`.
`hints_observed` records the exact hint values used so the policy
can be audited deterministically.

---

## 7. Workspace-level configuration

A workspace running this profile MAY publish a `routing_policy_uri`
in its description. The URI points at a document defining the
routing rules. CHAP places no constraints on what the document
contains; consistency is the operator's responsibility.

```json
{
  "workspace_id":         "wsp_support_triage",
  "profiles":             ["core/1.0", "review/1.0", "modes/1.0", "routing/1.0"],
  "routing_policy_uri":   "https://policies.techcorp.example/routing/v4"
}
```

The `policy_id` in any routing decision artefact MUST reference a
policy that resolves under this URI (or the workspace has no URI
and the policy_id is opaque).

---

## 8. Composition with other profiles

### With `review/1.0`

`review.depth` decides *whether* and *how thoroughly* to review.
`review.request` then carries out the review at that depth. The
two are explicitly chained: a typical flow is

```
task.complete → review.depth → (if not skip) review.request → decide.*
```

The `decision_artefact` from `review.depth` is `cited` by the
subsequent `review.request` envelope, so the chain is queryable
end-to-end.

### With `modes/1.0`

| Mode       | Routing profile honours…                                  |
|------------|-----------------------------------------------------------|
| `shadow`   | Routing decisions are *recorded* but not *enforced*. Every shadow output goes to a human regardless. |
| `trial`    | Same: full review per `modes` semantics, routing decisions logged for later analysis. |
| `production` | Routing decisions are enforced. `review.depth=skip` actually skips review. |

This is the discipline: modes own enforcement; routing owns the
recommendation. The two never conflict because modes is a strict
upper bound.

### With `deliberation/1.0`

When `review.depth` returns `full` and the workspace has
`deliberation/1.0`, the resulting `review.request` MAY open a
deliberation rather than a single-reviewer review. The routing
profile does not dictate this; the operator's policy does.

---

## 9. Confidence calibration: a caveat

Two `confidence: 0.83` values from different models are not
comparable without calibration data. CHAP does not standardise
calibration. Operators using `confidence` for routing SHOULD:

1. Restrict each rule to a single `model_id` or model family.
2. Maintain a calibration table that maps confidence → expected
   accuracy for each model.
3. Re-derive thresholds whenever a model is upgraded.

Cross-model routing rules ("escalate if confidence < 0.7") are
dangerous in heterogeneous deployments. The protocol cannot
prevent this; the practice should.

---

## 10. What this profile does not do

- **Decide whether to use an agent or a human.** That's the workspace
  designer's decision; `task.route` operates over candidates the
  designer has already declared eligible.
- **Define the cost model.** `cost_consumed_usd` is whatever the
  operator says it is. CHAP doesn't standardise per-token, per-API,
  or per-second costing.
- **Standardise the routing policy itself.** Policies are documents
  resolved via `routing_policy_uri`; their format is operator-defined.
- **Make routing decisions reversible.** A routing decision is an
  artefact; like all artefacts, it lives forever in the chain. Bad
  decisions are corrected by *subsequent* events, not by deletion.

---

## 11. Worked example

A senior support agent ("Maya") works in a workspace where
`routing/1.0` is enabled. An incoming refund request becomes
a task with hints:

```json
{
  "id":   "tsk_…",
  "kind": "refund_request",
  "routing_hints": {
    "criticality":  "high",
    "max_cost_usd": 50,
    "risk_tier":    "financial-tier-2",
    "deadline":     "2026-05-17T17:00:00Z"
  }
}
```

The Coordinator calls `task.route` with three candidates: a fast
agent, a careful agent, and a human pool. The routing policy
returns `agent:careful-draft-v2` (criticality=high routes to careful
tier; max_cost_usd=$50 rules out human pool). Decision artefact
recorded.

The careful agent produces a draft with measured hints:

```json
{
  "routing_hints": {
    "confidence":         0.62,
    "model_id":           "careful-draft-v2:2026-05",
    "cost_consumed_usd":  3.40,
    "latency_ms":         2810
  }
}
```

The Coordinator calls `review.depth`. The policy sees
criticality=high + confidence=0.62 and returns `full`. A reviewer
is summoned through `review/1.0`.

Before the reviewer arrives, the Coordinator calls `escalate.auto`.
A rule fires: criticality=high AND confidence<0.7 → group:senior-reviewers.
The task is escalated to a senior pool. Maya joins from that pool.

Every step is an artefact in the chain. The full audit shows: what
hints were on the task, which model produced what with what
confidence, which routing decisions fired, which rules triggered,
who eventually reviewed, what they overrode, why.

---

## 12. Schema reference

The schemas for `task.route`, `review.depth`, and `escalate.auto`
methods are in [`../schemas/profiles/chap-routing.schema.json`](../schemas/profiles/chap-routing.schema.json).
