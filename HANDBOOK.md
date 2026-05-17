# HAP Handbook

A practical guide to running HAP in real deployments. Where the
specification answers "what does the wire look like?", this handbook
answers "how do I actually use it?"

Read this if you're building or operating a HAP-based system.
Newcomers should start with the [README](./README.md) and the
[5-minute start](./examples/00-five-minute-start.md).

---

## Table of contents

1. [Concepts in 10 minutes](#1-concepts-in-10-minutes)
2. [Roles and responsibilities](#2-roles-and-responsibilities)
3. [Designing a workspace](#3-designing-a-workspace)
4. [Choosing profiles](#4-choosing-profiles)
5. [Rolling out a new agent](#5-rolling-out-a-new-agent)
6. [Capturing overrides as learning data](#6-capturing-overrides-as-learning-data)
7. [Identity, authentication, authorisation](#7-identity-authentication-authorisation)
8. [Audit, retention, and right-to-be-forgotten](#8-audit-retention-and-right-to-be-forgotten)
9. [Production deployment](#9-production-deployment)
10. [Monitoring and observability](#10-monitoring-and-observability)
11. [Incident response](#11-incident-response)
12. [Common patterns](#12-common-patterns)
13. [Anti-patterns](#13-anti-patterns)

---

## 1. Concepts in 10 minutes

A HAP deployment has three kinds of moving part:

**Workspaces.** A workspace is a named context. Inside it, a defined
set of participants do work, send messages, delegate tasks, and
build an audit log. A team's customer-support triage is one
workspace; the same team's release-decision board is another. They
don't share state.

**Participants.** Humans, agents, services, or groups, each
identified by a URI:

```
human:[email protected]        — a person
agent:triage-bot#v3.2              — a specific agent version
service:[email protected]   — a service or component
group:on-call@example.org          — a named group of participants
workspace:wsp_release-decisions    — a workspace acting as a peer
```

**Methods.** The verbs participants exchange. Core is seven methods
that all implementations support. Profiles add more — `review.request`,
`decide.override`, `abstain.declare`, and so on.

Underneath everything is an **audit log**: every accepted envelope
is appended in arrival order. This log is the source of truth for
what happened, who decided what, and on what basis.

If you remember three things:

- **Workspace** = a context with members.
- **Task** = a delegated piece of work with a state machine.
- **Audit log** = the immutable record of everything.

---

## 2. Roles and responsibilities

HAP recognises five role categories. The protocol itself doesn't
enforce them — your workspace policy does — but the categories are
consistent across implementations.

| Role        | Typical work                                              |
|-------------|-----------------------------------------------------------|
| **Drafter** | Produces draft output. Usually an agent.                  |
| **Reviewer** | Approves, rejects, or overrides drafts. Usually a human. |
| **Operator** | Runs the workspace itself. Pauses, resumes, snapshots, promotes modes. Privileged. |
| **Auditor**  | Reads the audit log; produces reports. Read-only.        |
| **Bridge**   | Represents an external A2A peer inside the workspace.    |

A single participant can hold multiple roles in different workspaces.
A human can be a Reviewer in one workspace and a Drafter
("I wrote the policy doc, please review") in another. An agent can
be a Drafter in `trial` mode and an Operator-equivalent for its
own retraining workspace.

---

## 3. Designing a workspace

Three decisions shape every workspace:

### 3.1 Scope

Keep workspaces narrow. "Customer support triage" is a workspace.
"Refund decisions over £200" is a different workspace. "Release
approvals" is a third. The benefits:

- Audit trails are queryable by workspace, not by tag.
- Policy (who can do what) lives at the workspace.
- Modes are per-workspace; you can promote one to `production`
  without affecting another.
- Performance is per-workspace; a hot one doesn't crowd a cold one.

### 3.2 Membership policy

Decide who can join, who can be a Drafter, who can be a Reviewer,
and how Operators are appointed. Common shapes:

| Policy            | Means                                                  |
|-------------------|--------------------------------------------------------|
| Closed            | Operator explicitly admits each participant.           |
| Open within org   | Anyone with the right OIDC scope auto-joins.           |
| Federation        | Members of named partner workspaces auto-join via bridge participants. |

### 3.3 Decision policy

For each kind of task: who approves, what counts as approved, how
disagreements escalate. The [`review`](./profiles/review.md) and
[`deliberation`](./profiles/deliberation.md) profiles give you the
mechanisms; your policy maps task kinds onto rules:

```yaml
# Example policy (deployment-specific, not standardised)
task_kinds:
  draft_response:
    review:        required
    rule:          any_one_approves
    override_tags: [tone-softened, severity-downgraded, factual-fix]
  refund_over_500:
    review:        required
    rule:          quorum:2
  release_decision:
    review:        required
    rule:          weighted_vote_with_veto:2.0
    weights:       { eng-lead: 1.0, security: 1.0, product: 1.0 }
    veto:          { security: true }
```

---

## 4. Choosing profiles

Start with Core. Add profiles as workflow needs become concrete.

**If humans review agent output** → add `review`. This is the most
valuable profile; the override-as-data dividend pays for itself.

**If you're rolling out new agents** → add `modes`. The
shadow/trial/production ladder is how you avoid surprises.

**If quick interrupt-style disambiguation is common** → add `whisper`.
Use it for "should I cancel this order or confirm with the
customer?" — closed-set, time-bound, with a default if no one
answers.

**If multiple humans must agree** → add `deliberation`. Quorum,
weighted votes, vetoes.

**If shifts change or work routes between humans** → add `handoff`.

**If you need a production control plane** (pause an agent, snapshot
a workspace, roll back a misconfigured policy) → add `control`.

**If non-repudiation matters** → add `security-signed`. Every
message becomes Ed25519-signed.

**If regulatory audit matters** → add `audit-scitt`. The audit log
becomes a SCITT transparency service.

**If verified human identity matters** → add `identity-oidc`. Step-up
auth, `cnf.jwk` binding.

**If cross-org or regulated-profession identity matters** → add
`identity-vc`. W3C Verifiable Credentials.

A typical production deployment is:

```
core/1.0 + review/1.0 + modes/1.0 + identity-oidc/1.0 + security-signed/1.0
```

A regulated deployment adds:

```
+ audit-scitt/1.0 + deliberation/1.0
```

---

## 5. Rolling out a new agent

The `modes` profile makes rollout a protocol-level operation rather
than tribal knowledge. The standard sequence:

```
shadow → trial → production
```

### 5.1 Shadow (1–4 weeks)

The new agent processes real traffic, but its output is **not
delivered** to the end recipient. Output goes only to a shadow
observers list, which compares it against the live flow's output.

Promotion criteria (typical):

- Output matches live flow ≥ 95% per task kind.
- No protocol-level errors (no malformed envelopes, no illegal
  state transitions).
- Throughput meets the SLA.

### 5.2 Trial (1–2 weeks)

The agent's output **is** delivered to the recipient — but **every
output is reviewed**. The reviewer can approve, reject, or override.

The override rate is the primary input to the promote-to-production
decision. Watch:

| Metric                         | What it tells you                              |
|--------------------------------|------------------------------------------------|
| Overall override rate          | How often humans edit the output.              |
| Override-rate by tag           | What kinds of edits dominate (tone, accuracy, etc.). |
| Abstention rate                | Are reviewers routinely declining? Re-scope.   |
| Time-to-review                 | Is the human bandwidth there?                  |

A common threshold: promote when override rate has been under your
target (e.g. 10%) for two consecutive weeks.

### 5.3 Production

Review becomes per-policy: random sampling, risk-triggered, or none.
The override-capture infrastructure stays on — you still want the
learning signal — but it covers a fraction of traffic.

### 5.4 Demoting

A regression in production override rate, an incident, or a policy
change can demote an agent back to `trial` or `shadow`. The
`control.set_mode_ceiling` operation records the change in the
audit log.

---

## 6. Capturing overrides as learning data

The single most valuable property of HAP is that **every
override is structured data by construction**. This section
explains how to actually use that data.

### 6.1 What's in an override

An override carries four fields:

| Field         | Meaning                                                  |
|---------------|----------------------------------------------------------|
| `diff`        | RFC 6902 JSON Patch — the exact edit.                    |
| `rationale`   | Free-text explanation of why.                            |
| `tags`        | Categorical labels (`tone-softened`, `severity-downgraded`, `factual-fix`). |
| `policy_refs` | References to the guideline(s) the override implements.  |

### 6.2 Querying overrides

Read the audit log filtering for `decide.override`:

```json
{
  "method": "audit.read",
  "params": {
    "workspace": "wsp_code_review",
    "filter":    { "method": "decide.override", "ts_range": { "from": "2026-05-01", "to": "2026-05-17" } }
  }
}
```

You get an array of override envelopes. Aggregate by `tags`, by
`from` (which reviewer), by `policy_refs`, or by the originating
agent (which is in the based-on artefact's metadata).

### 6.3 What to do with the aggregates

- **Tune system prompts.** A spike of `tone-softened` overrides
  for one agent means its prompts probably over-index on urgency.
- **Tune classifiers.** A spike of `severity-downgraded` overrides
  on a code-review agent means its severity rubric is too eager.
- **Detect drift.** A change in the tag distribution week-over-week
  is a leading indicator of agent drift before the override rate
  itself moves.
- **Target retraining.** Use the override pairs (before/after) as
  preference data for fine-tuning.
- **Codify guidelines.** Frequently-cited `policy_refs` tell you
  which guidelines are doing work; ones never cited are dead text.

### 6.4 A weekly review cadence

A minimal cadence that pays for itself:

| Day      | Action                                                        |
|----------|---------------------------------------------------------------|
| Monday   | Pull last week's overrides; aggregate by tag and agent.       |
| Tuesday  | Top three tags → discuss in product/engineering sync.         |
| Wednesday | One concrete change per tag (prompt edit, rubric edit, retrain). |
| Thursday | Push change to `trial` mode of next agent version.            |
| Friday   | Compare shadow output against current live output.            |

Two weeks of this and the agent is measurably better than where it
started. The override capture didn't create work; it created
visibility.

---

## 7. Identity, authentication, authorisation

### 7.1 Layers

Three layers, each with a defined responsibility:

| Layer          | Question it answers                       | Standard                |
|----------------|-------------------------------------------|-------------------------|
| Authentication | Who is this party?                        | OIDC, W3C VC, SPIFFE    |
| Key binding    | What key are they signing with?           | `cnf.jwk` (RFC 7800), DPoP (RFC 9449) |
| Authorisation  | What can they do in this workspace?       | Your policy             |

### 7.2 Picking the identity profile

| Use case                                                    | Profile          |
|-------------------------------------------------------------|------------------|
| Internal SaaS, single IdP, employees only                   | `identity-oidc`  |
| Regulated profession (clinicians, lawyers, accountants)     | `identity-vc`    |
| Cross-organisation, no shared IdP                           | `identity-vc`    |
| Service-to-service in a service mesh                       | OIDC client-credentials or SPIFFE (use `identity-oidc` for the binding format) |

You can use OIDC and VC in the same workspace for different
participant categories.

### 7.3 Step-up auth

Privileged operations — anything in the `control` profile, plus
mode promotion and policy changes — require step-up authentication.
The pattern:

```
operation issued
   │
   ▼
Coordinator checks id_token.auth_time
   │
   ├── recent (< step_up_window) → proceed
   │
   └── stale → return -32402, client triggers prompt=login
```

The default window is 5 minutes. Configure via the workspace
descriptor.

### 7.4 Mapping OIDC scopes to HAP roles

A reasonable starting map:

```yaml
oidc_scope_to_role:
  hap.read:    auditor
  hap.user:    [drafter, reviewer]
  hap.admin:   operator
  hap.audit:   auditor
```

Role-to-method permissions live in workspace policy. Example:

```yaml
role_permissions:
  auditor:    [audit.read]
  drafter:    [task.update, task.complete, message.post]
  reviewer:   [decide.approve, decide.reject, decide.override, abstain.declare, escalate.raise]
  operator:   [control.*, workspace.*, participant.evict]
```

---

## 8. Audit, retention, and right-to-be-forgotten

### 8.1 What the audit log contains

Every accepted envelope, verbatim, in arrival order, with the
Coordinator's arrival timestamp. With `security-signed`, the
signatures are preserved; with `audit-scitt`, each entry produces a
SCITT receipt.

### 8.2 Retention

Retention is deployment policy, not protocol. Common settings:

| Workspace kind             | Typical retention      |
|----------------------------|-----------------------|
| Operational (support, ops) | 1–2 years             |
| Compliance-relevant        | 7 years               |
| Healthcare-regulated       | 10+ years per jurisdiction |
| Federal-finance            | Per regulation        |

### 8.3 Right-to-be-forgotten

Append-only logs and GDPR's erasure right are in genuine tension.
HAP's recommended pattern:

1. **Don't store personal data in envelopes that don't need it.**
   Pseudonymise wherever possible: refer to a customer by an opaque
   id, not by name in the envelope body.
2. **Use the redaction registry.** Personal data that does end up
   in the log is referenced by a redaction key; when a subject
   exercises erasure rights, the key is rotated and the cleartext
   is removed from any side-store, leaving the envelope's hash
   intact but its referenced cleartext unrecoverable.
3. **Document the policy.** In your workspace's descriptor, link
   to your data-handling policy so subject-rights requests have a
   defined path.

The protocol doesn't mandate a specific redaction mechanism; it
provides the hooks. See [`SECURITY.md`](./SECURITY.md) §6 for the
mechanism.

### 8.4 Auditor access

An Auditor reads the log, never writes to it. With the `audit-scitt`
profile, Auditors verify SCITT receipts offline — they don't need
the Coordinator's cooperation to confirm an entry is genuine.

---

## 9. Production deployment

A reference production deployment is described fully in
[`integrations/HAP-deployment-patterns.md`](./integrations/HAP-deployment-patterns.md).
The essentials:

### 9.1 Topology

| Component                | What it does                                  | Typical implementation       |
|--------------------------|-----------------------------------------------|------------------------------|
| Coordinator              | Accepts envelopes, routes, appends audit log. | Stateful service (1+ replicas) |
| Audit store              | Durable storage of the log.                   | Postgres, S3, or SCITT       |
| Identity provider        | OIDC tokens, key issuance.                    | Okta, Auth0, Keycloak, etc.  |
| SCITT service (optional) | Transparency log + receipts.                  | A SCITT-compliant service    |
| Participant clients      | Humans (UI), agents (libraries), services.    | Implementation-specific      |

### 9.2 Transport

| Transport       | When                                       |
|-----------------|--------------------------------------------|
| HTTP POST       | Default; works through any proxy and CDN.  |
| WebSocket       | Interactive UIs that need server push.     |
| HTTP + SSE      | Firewall-friendly server push fallback.    |
| Kafka / NATS    | High-throughput server-to-server flows.    |

### 9.3 Sizing

A single Coordinator instance handles thousands of envelopes per
second on commodity hardware. The audit store is the typical
bottleneck; size it for ~10× your peak envelope rate to leave
headroom for audit queries.

### 9.4 High availability

Coordinator stateless behind a load balancer; audit store
replicated; identity provider per its own HA story. Cross-region
needs careful audit-store replication (eventual vs synchronous);
default to one region per workspace and federate via the bridge
pattern.

---

## 10. Monitoring and observability

A useful HAP deployment publishes:

| Metric                                          | Source                              |
|-------------------------------------------------|-------------------------------------|
| Envelopes/sec by method                         | Coordinator                         |
| p50/p95/p99 envelope handling latency           | Coordinator                         |
| Override rate by task kind                       | Audit query                         |
| Abstention rate by participant                   | Audit query                         |
| Time-to-review (review.request → decide.*)       | Audit query                         |
| Mode promotion events                            | `control.*` audit entries           |
| Active participants per workspace                | `workspace.describe`                |
| SCITT receipt latency (if audit-scitt enabled)   | SCITT service                       |

The audit log is the source of truth; metrics derived from it
require no instrumentation of the participants themselves.

---

## 11. Incident response

When something goes wrong with an agent:

### 11.1 Stop the bleeding

```json
{
  "method": "control.pause",
  "params": {
    "scope":           "participant",
    "participant_uri": "agent:triage-bot#v3.3",
    "reason":          "Override-rate spike: 38% in last 15 minutes.",
    "in_flight_policy": "allow_to_complete"
  }
}
```

The agent stops accepting new work immediately. In-flight tasks
complete normally (you don't want abandoned half-states).

### 11.2 Demote the mode

```json
{
  "method": "control.set_mode_ceiling",
  "params": { "new_ceiling": "trial" }
}
```

Future tasks for this workspace cannot run in `production`; they
all go through review.

### 11.3 Snapshot, investigate, decide

```json
{ "method": "control.snapshot", "params": { "label": "pre-rollback-investigation" } }
```

Use the snapshot to gather state for the incident review. The
audit log between the snapshot and now contains everything; query
it for the symptomatic methods.

### 11.4 Roll back if appropriate

If a misconfiguration is the cause, `control.rollback` restores the
named snapshot — appending, never truncating, the audit log.

### 11.5 Reactivate

Promote modes back up when the fix is verified:

```json
{ "method": "control.set_mode_ceiling", "params": { "new_ceiling": "production" } }
```

And resume:

```json
{ "method": "control.resume", "params": { "scope": "participant", "participant_uri": "agent:triage-bot#v3.3" } }
```

The full incident is reconstructible from the audit log: when the
pause happened, who issued it, what was rolled back, and when normal
operations resumed.

---

## 12. Common patterns

### 12.1 Drafter–Reviewer

The classic. An agent drafts; a human approves or overrides.

```
human creates task → agent drafts → review.request → human decides → done
```

Use `review` + (optionally) `modes`. See [`examples/03-review-and-approve.md`](./examples/03-review-and-approve.md).

### 12.2 Drafter–Reviewer–Approver

Two-step approval for higher-stakes decisions.

```
human creates task → agent drafts → reviewer approves → approver final-approves → done
```

Use `review` + `deliberation` with `rule: all_approve`.

### 12.3 Three-Reviewer Quorum

For regulated decisions.

```
agent drafts → deliberate.open(rule: quorum:2) → three reviewers vote → close
```

See [`examples/08-multi-human-deliberation.md`](./examples/08-multi-human-deliberation.md).

### 12.4 Mid-task whisper

Agent doesn't have enough information; quick interrupt before
proceeding.

```
agent starts task → whisper.ask → human chooses → agent proceeds → review.request
```

See [`examples/06-whisper-prompt.md`](./examples/06-whisper-prompt.md).

### 12.5 Follow-the-sun handoff

```
shift-A-handler → handoff.propose(group:on-call) → shift-B-handler accepts → continues
```

See [`examples/07-handoff-shift-change.md`](./examples/07-handoff-shift-change.md).

### 12.6 Federation via A2A bridge

Cross-organisation work without exposing the full workspace.

```
internal workspace → service:bridge.partner → A2A → partner org
```

The bridge participant is a workspace member; A2A traffic is its
internal concern. See [`integrations/HAP-with-A2A.md`](./integrations/HAP-with-A2A.md).

### 12.7 Tool-using agent with MCP

```
human creates task → agent calls MCP tool → result cited in artefact → review.request → done
```

See [`integrations/HAP-with-MCP.md`](./integrations/HAP-with-MCP.md).

---

## 13. Anti-patterns

Things HAP doesn't stop you from doing but you shouldn't.

**One huge workspace for everything.** Per §3.1, narrow workspaces
beat wide ones. Audit queries get slow, policy gets tangled, mode
promotion affects unrelated work.

**Skipping `review` on agent output for "small" tasks.** Without
`review`, you have no override-capture signal. Even if you don't
need approval, the structured-edit data is the point.

**Free-text answers in `whisper`.** Defeats aggregation. Always
provide a closed option set unless free text is genuinely needed.

**Replacing the audit log when GDPR erasure is requested.** Use the
redaction-key mechanism (see [§8.3](#83-right-to-be-forgotten)).
Truncating the log breaks every signature chain and every SCITT
receipt downstream.

**Treating `mode_ceiling` as configuration.** It's a privileged
operation, audited, with step-up auth required. Don't read it from
an unsigned config file.

**Per-participant audit logs.** The log is workspace-scoped. A
participant doesn't have its own log; it has its messages in
others' logs. (To get a participant-centric view, query the
workspace log with `filter.from = participant_uri`.)

**Custom URI schemes inside the protocol.** Reuse the five HAP URI
schemes (`human:`, `agent:`, `service:`, `group:`, `workspace:`)
plus DNS or DID authorities. Inventing your own breaks interop.

**Encapsulating MCP or A2A traffic in HAP envelopes.** Cite them;
don't copy them. The HAP audit log links to MCP transcripts, it
doesn't contain them.

---

## Where to next

- For envelope-level detail: [`core/SPEC.md`](./core/SPEC.md) and
  the relevant profile in [`profiles/`](./profiles/).
- For end-to-end worked scenarios: [`examples/`](./examples/).
- For composition with adjacent protocols: [`integrations/`](./integrations/).
- For frequently asked questions: [`FAQ.md`](./FAQ.md).
