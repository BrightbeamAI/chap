# Profile: `routing`

**Profile id:** `routing/1.0` · **Depends on:** Core · **Composes with:** `review/1.0`, `modes/1.0`

Decide *how* a task gets handled, who picks it up, how deep the
review is, when to auto-escalate, based on the runtime signals
already carried in `routing_hints` on tasks and artefacts.

This profile adds three decision methods. It does not invent new
signals; it consumes the ones Core already defines and stores the
decisions it makes as artefacts.

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
format: each request is recorded, and the response carries the
decision.

The fractional hints, `confidence`, `max_cost_usd` and
`cost_consumed_usd`, are carried as decimal strings (`"0.62"`, not
`0.62`). Envelope numbers are integers, so that canonicalisation and
therefore the audit hash agree across implementations; see
[SPECIFICATION.md §5.2](../SPECIFICATION.md#52-signing-algorithm). A JSON number with a
fractional part is rejected at ingress with `-32602`. Policy code reads
the string and parses it.

---

## 2. New methods

| Method            | Type    | Summary                                          |
|-------------------|---------|--------------------------------------------------|
| `task.route`      | request | Pick an assignee for a task from candidates, given hints. |
| `review.depth`    | request | Decide review depth for an artefact: skip, spot-check, full. |
| `escalate.auto`   | request | Evaluate auto-escalation rules against a task's routing hints. |

Each method stores its decision, with the hints consulted, as a
`route_decision` artefact in the workspace's state and returns its id
as `decision_artefact`. The audit log records the request only, and no
method returns a stored decision, so consumers keep the response.

The caller in `from` MUST be a workspace member, as for every method
outside the exemptions in [SPECIFICATION.md §6.3.1](../SPECIFICATION.md#631-actor-membership-precondition).
A call from anyone else is refused with `-32011` and leaves nothing on
the log.

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
    "from":        "human:jordan-ops@example.org",
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

The `decision_artefact` is the id of a new `route_decision` artefact
that captures the inputs (the task's hints), the policy id, and the
selected assignee. It is kept in the workspace's state; the audit log
holds the `task.route` request only (§2).

After `task.route` succeeds, the Coordinator MUST set the task's
`assignee` to `selected`, and it changes nothing else, in every mode.

A participant paused under `control/1.0` is assigned no new tasks
([`control.md`](./control.md) §2). The default policy, which takes the
first candidate that is a workspace member, passes over a paused one and
lists it in `alternatives_considered` with the reason `paused`. A
deployment's policy that selects a paused participant is refused with
`-32063`, and the task keeps its assignee.

### `task.route` error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32510`  | `no_eligible_assignee`: no candidate is a workspace member who is not paused, or the policy chose one who is not a member. |
| `-32511`  | `routing_policy_violation`. Reserved; no Coordinator returns it. |
| `-32513`  | `candidates_empty`: `candidates` array was empty. |
| `-32515`  | `policy_unreachable`: the deployment's policy raised an error. |
| `-32063`  | The policy chose a participant who is paused. |

---

## 4. `review.depth`

Given an artefact and its hints, decide whether to skip review,
spot-check, or do a full review. Returns a depth tier with rationale.

```json
{
  "method": "review.depth",
  "params": {
    "workspace":      "wsp_support_triage",
    "from":           "human:jordan-ops@example.org",
    "task_id":        "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "artefact_routing_hints": {
      "confidence": "0.91",
      "model_id":   "draft-bot:2026-05"
    },
    "ts":             "2026-05-17T09:14:48Z"
  }
}
```

The Coordinator merges the task's `routing_hints` with the optional
`artefact_routing_hints`, which carries the artefact's own hints; a key
in `artefact_routing_hints` takes precedence. Neither reference
Coordinator reads an `artefact_id`.

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
| `spot_check` | Random sampling. `sampling_probability` is in [0, 1]; the caller applies it. |
| `full`       | Every artefact reviewed.                                 |
| `escalated`  | Reserved; the depth-decider has invoked `escalate.auto`. |

`review.depth` records a recommendation. The Coordinator neither
samples nor changes `review_required` or reviewers; the caller acts on
it.

### `review.depth` error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32514`  | `depth_not_applicable`: no routing hint on the task or in `artefact_routing_hints`. |
| `-32515`  | `policy_unreachable`: the deployment's policy raised an error. |

---

## 5. `escalate.auto`

Evaluate the workspace's auto-escalation rules against a task's
routing hints. Returns whether to escalate and to whom. When a rule
fires, the response names it in `triggered_rule`, and the stored
decision keeps its id and summary.

```json
{
  "method": "escalate.auto",
  "params": {
    "workspace":   "wsp_support_triage",
    "from":        "human:jordan-ops@example.org",
    "task_id":     "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "default_escalation_target": "group:senior-reviewers@example.org",
    "ts":          "2026-05-17T09:15:30Z"
  }
}
```

`escalate.auto` reads the task's `routing_hints`. The optional
`default_escalation_target` names the target for the built-in rule; a
deployment's `escalationPolicy` or `escalation_policy` hook chooses its
own target.

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

`escalate.auto` creates no task and leaves the task's state alone. It
stores the decision, calls the deployment's `onAutoEscalate` or
`on_auto_escalate` hook where set, and returns the target. A caller
that wants the escalation sends `escalate.raise`.

### `escalate.auto` error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32512`  | `auto_escalation_triggered`. Reserved; no Coordinator returns it. |
| `-32515`  | `policy_unreachable`: the deployment's policy raised an error. |
| `-32516`  | `escalation_target_unavailable`: an escalation fired with no target, or the target is neither a workspace member nor a `group:` address. |

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
      "confidence":  "0.91",
      "model_id":    "draft-bot:2026-05"
    },
    "rationale": "criticality=low + confidence>0.85: sampled at 10%."
  }
}
```

`decision_type` is one of `task.route`, `review.depth`, `escalate.auto`.
`outcome` is the selected URI for `task.route`, the depth tier for
`review.depth`, and an object `{escalate, to}` for `escalate.auto`.
`hints_observed` records the exact hint values used so the policy
can be audited deterministically. Neither reference Coordinator
computes a `content_hash` for a route decision.

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
`review.request` then carries out the review at that depth. A typical
flow is

```
task.complete → review.depth → (if not skip) review.request → decide.*
```

The Coordinator does not link the two. A caller that wants the link on
the audit log can cite the `decision_artefact` from `review.depth` in
the `review.request` params, which the log records as sent.

### With `modes/1.0`

Routing behaves alike in every mode: `task.route` reassigns; the other
two only recommend. This is the discipline: modes own enforcement;
routing owns the recommendation.

### With `deliberation/1.0`

`review.request` always opens a review. When `review.depth` returns
`full` and the operator wants a group decision, the caller opens one
with `deliberate.open` (`deliberation/1.0`). The routing profile
leaves that choice to the operator's policy.

---

## 9. Confidence calibration: a caveat

Two `confidence: "0.83"` values from different models are not
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
- **Make routing decisions reversible.** A routing decision is stored
  as an artefact that no method deletes, and the request that produced
  it stays on the audit log. Bad decisions are corrected by
  *subsequent* events.

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

The caller sends `task.route` with three candidates: a fast agent, a
careful agent, and a human pool. The routing policy returns
`agent:careful-draft-v2` (criticality=high routes to careful tier;
max_cost_usd=$50 rules out human pool). The Coordinator reassigns the
task and stores the decision artefact.

The careful agent produces a draft with measured hints:

```json
{
  "routing_hints": {
    "confidence":         "0.62",
    "model_id":           "careful-draft-v2:2026-05",
    "cost_consumed_usd":  "3.40",
    "latency_ms":         2810
  }
}
```

The caller sends `review.depth` with the draft's hints in
`artefact_routing_hints`. The policy sees criticality=high +
confidence=0.62 and returns `full`. The caller summons a reviewer with
`review.request`.

Before the reviewer arrives, the caller sends `escalate.auto`. The
operator's rule reads the task's hints and fires: criticality=high AND
risk_tier=financial-tier-2 → group:senior-reviewers.
The caller then sends `escalate.raise`, which hands the task to the
senior pool. Maya joins from that pool.

Every call is on the audit log, and the caller keeps each routing
response. Together they show what hints were on the task, which model
produced what with what confidence, which routing decisions fired,
which rules triggered, who eventually reviewed, what they overrode,
and why.

---

## 12. Schema reference

The schemas for `task.route`, `review.depth`, and `escalate.auto`
methods are in [`../schemas/profiles/chap-routing.schema.json`](../schemas/profiles/chap-routing.schema.json).
