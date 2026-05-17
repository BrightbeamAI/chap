# Profile: `modes`

**Profile id:** `modes/1.0` · **Depends on:** Core

The `modes` profile adds a **promotion ladder** for tasks and
workspaces: `shadow` → `trial` → `production`. This is how you
safely roll out a new agent: run it alongside the existing flow
(shadow), then deliver its output under review (trial), then trust
it in production.

This profile adds no new methods. It adds typed fields and policy
enforcement.

---

## 1. The three modes

| Mode         | Output delivered? | Reviewed? | Used for                            |
|--------------|-------------------|-----------|-------------------------------------|
| `shadow`     | No                | n/a       | Side-by-side comparison against existing flow. |
| `trial`      | Yes               | Every output | Gated rollout of a new agent or version. |
| `production` | Yes               | Per policy (may be sampled) | Steady-state operation. |

A new agent version SHOULD spend time in `shadow` mode (typically
1–4 weeks) before promotion to `trial`, and in `trial` (typically
1–2 weeks) before `production`. Concrete promotion criteria are
deployment-specific; common ones are listed in §5.

---

## 2. New fields

On `workspace.describe`:

```json
{
  "mode":         "production",
  "mode_ceiling": "production"
}
```

`mode` is the workspace's current default mode for new tasks.
`mode_ceiling` is the highest mode any task in this workspace may
use; it's a safety bound that requires elevated privilege to raise.

On `task.create`:

```json
{
  "method": "task.create",
  "params": {
    "...":  "...",
    "mode": "trial"
  }
}
```

The Coordinator MUST reject a `task.create` whose `mode` exceeds
the workspace's `mode_ceiling` with error `-32040`.

---

## 3. Behaviour by mode

### 3.1 `shadow`

- The task runs to completion.
- The output is recorded in the audit log.
- The output is **not delivered** to the nominal recipient. (It may
  be delivered to a `shadow_observers` list for analysis.)
- Reviews are not requested.

This lets a new agent process real traffic without affecting users.
Comparing the shadow output to the live flow's output is the
primary input to promotion decisions.

### 3.2 `trial`

- The task runs to completion.
- The output is delivered.
- Review is mandatory regardless of any per-task `review.required`
  field — `trial` mode forces review on.

This is the "every output gets human eyes" mode. Override rate in
trial mode is the most important signal for whether to promote.

### 3.3 `production`

- The task runs to completion.
- The output is delivered.
- Review is per workspace policy. Common policies: random sampling
  (e.g. 5%), risk-triggered (e.g. for high-value cases), or none.

---

## 4. Mode transitions

A workspace's `mode_ceiling` is changed by a privileged operation
that records the change in the audit log:

```json
{
  "method": "control.set_mode_ceiling",
  "params": {
    "workspace":  "wsp_demo",
    "from":       "human:admin@example.org",
    "to":         "service:coordinator@example.org",
    "ts":        "2026-05-17T17:00:00Z",
    "new_ceiling": "production",
    "reason":     "Trial complete; override rate < 5% for 2 weeks; promoting."
  }
}
```

This method is provided by the `control` profile, which is strongly
recommended alongside `modes`.

---

## 5. Promotion criteria (recommended, not normative)

Common criteria for moving from one mode to the next:

| Transition                     | Typical signal                                  |
|--------------------------------|-------------------------------------------------|
| `shadow` → `trial`             | Shadow output matches live flow ≥ 95% (per kind). |
| `trial` → `production`         | Override rate < threshold for N consecutive days. Abstention rate stable. |
| `production` → `trial` (demote) | Incident or override-rate spike (see [`control.md`](./control.md)). |

These are policy, not protocol. The protocol provides the data —
your governance picks the thresholds.

---

## 6. Error codes

| Code      | Meaning                                                     |
|-----------|-------------------------------------------------------------|
| `-32040`  | Task mode exceeds the workspace's `mode_ceiling`.           |
| `-32041`  | Promotion requires elevated privilege (step-up auth, etc.). |

---

## 7. Composition notes

- **With `review`:** trial-mode tasks have implicit
  `review.required = true` regardless of the task's own setting.
- **With `control`:** `control.set_mode_ceiling`, snapshots, and
  rollbacks cover the operational side of mode changes.
- **With `security-signed`:** mode-ceiling changes are privileged
  and SHOULD require step-up authentication.

---

## 8. Worked example

Mode promotion is implicit in [`../examples/09-pause-resume-rollback.md`](../examples/09-pause-resume-rollback.md).
