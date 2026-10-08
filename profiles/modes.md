# Profile: `modes`

**Profile id:** `modes/1.0` · **Depends on:** Core

The `modes` profile adds a **promotion ladder** for tasks and
workspaces: `shadow` → `trial` → `production`. This is how you
safely roll out a new agent: run it alongside the existing flow
(shadow), then deliver its output under review (trial), then trust
it in production.

This profile's one method, `workspace.set_mode`, is specified and not
yet built. The profile adds typed fields and policy enforcement.

---

## 1. The three modes

| Mode         | Output delivered? | Reviewed? | Used for                            |
|--------------|-------------------|-----------|-------------------------------------|
| `shadow`     | No                | When the task sets `review_required` | Side-by-side comparison against existing flow. |
| `trial`      | Yes               | Every output | Gated rollout of a new agent or version. |
| `production` | Yes               | When the task sets `review_required` | Steady-state operation. |

A new agent version SHOULD spend time in `shadow` mode (typically
1-4 weeks) before promotion to `trial`, and in `trial` (typically
1-2 weeks) before `production`. Concrete promotion criteria are
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

**These semantics apply only where this profile is loaded.** `modes/1.0`
depends on Core but Core does not depend on it. Outside this profile,
`trial` does not force review. Two parts of mode handling run in every
workspace: `workspace.create` takes `mode` (default `trial`) and
`mode_ceiling` (default `production`) without checking either, and
`task.create` refuses a mode above the ceiling, or outside the ladder,
with `-32040`. No method changes `mode` after creation. A Coordinator
that forced review regardless would be imposing a profile nobody opted
into.

`mode_ceiling` is the highest mode any task in this workspace may
use. It is a safety bound, and raising it requires elevated privilege;
neither reference Coordinator checks a role for it yet.

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

- The Coordinator handles a shadow task like any other and records
  every call.
- Holding output back, or sending it only to `shadow_observers`, is
  the deployment's job (SPECIFICATION §11.3).
- Review is not forced; a shadow task that sets `review_required` is
  reviewed.

This lets a new agent process real traffic without affecting users.
Comparing the shadow output to the live flow's output is the
primary input to promotion decisions.

### 3.2 `trial`

- The task runs to completion.
- The output is delivered.
- Review is mandatory regardless of the task's own `review_required`
  field: `trial` mode sets it on every task that `task.create` or
  `control.supersede` creates.
- Because review is mandatory, `task.complete` opens a review rather
  than completing the task. A reviewer decision completes it.
- The Coordinator addresses that review to the human members other than
  the completer and the assignee, and refuses the completion with
  `-32011` where none qualifies. A trial workspace therefore needs at
  least one human who is neither. See [`review.md`](./review.md) §3.1.

This is the "every output gets human eyes" mode. Override rate in
trial mode is the most important signal for whether to promote.

### 3.3 `production`

- The task runs to completion.
- The output is delivered.
- The task is reviewed when it sets `review_required`. The deployment
  decides when to set it, for example by random sampling (e.g. 5%) or
  for high-value cases.

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

These are policy, not protocol. The protocol provides the data.
your governance picks the thresholds.

---

## 6. Error codes

| Code      | Meaning                                                     |
|-----------|-------------------------------------------------------------|
| `-32040`  | Task mode exceeds the workspace's `mode_ceiling`.           |
| `-32041`  | Allocated; no Coordinator returns it. A stale step-up is refused with `-32402`. |

---

## 7. Composition notes

- **With `review`:** a trial-mode task created by `task.create` or
  `control.supersede` has `review_required` set to true, whatever the
  task's own setting.
- **With `control`:** `control.set_mode_ceiling`, snapshots, and
  rollbacks cover the operational side of mode changes.
- **With `identity-oidc`:** where step-up is enforced,
  `control.set_mode_ceiling` needs a fresh `auth_time`.

---

## 8. Worked example

No worked example covers promotion yet.
