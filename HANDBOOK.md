# CHAP Handbook

A practical guide to running CHAP in real deployments. Where the
specification answers "what does the wire look like?", this handbook
answers "how do I actually use it?"

Read this if you're building or operating a CHAP-based system.
Newcomers should start with the [README](./README.md) and
[START_HERE.md](./START_HERE.md), which gets you to one recorded
decision before any of this matters.

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

A CHAP deployment has three kinds of moving part:

**Workspaces.** A workspace is a named context. Inside it, a defined
set of participants do work, send messages, delegate tasks, and
build an audit log. A team's customer-support triage is one
workspace; the same team's release-decision board is another. They
don't share state.

**Participants.** Humans, agents, services, or groups, each
identified by a URI:

```
human:alice@example.org   a person
agent:triage-bot#v3.2   a specific agent version
service:coordinator@example.org   a service or component
group:on-call@example.org   a named group of participants
workspace:wsp_release-decisions   a workspace acting as a peer
```

**Methods.** The verbs participants exchange. Core is a small set of
methods that every implementation supports. Profiles add more, `review.request`,
`decide.override`, `abstain.declare`, and so on.

Underneath everything is an **audit log**: every accepted
state-changing envelope is appended in arrival order, and from 0.3.0
so is a member's governed attempt that the Coordinator refuses, marked
as refused. Reads are not recorded. This log is the
source of truth for what happened, who decided what, and on what
basis.

If you remember three things:

- **Workspace** = a context with members.
- **Task** = a delegated piece of work with a state machine.
- **Audit log** = the append-only record of every state-changing call.

---

## 2. Roles and responsibilities

CHAP recognises five role categories. The protocol itself doesn't
enforce them (your deployment's policy does) but the categories are
consistent across implementations.

| Role        | Typical work                                              |
|-------------|-----------------------------------------------------------|
| **Drafter** | Produces draft output. Usually an agent.                  |
| **Reviewer** | Approves, rejects, or overrides drafts. Usually a human. |
| **Operator** | Runs the workspace itself. Pauses, resumes, snapshots, sets the mode ceiling. Privileged. |
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
- Mode ceilings are per workspace; you can raise one to `production`
  without affecting another. A workspace's own mode is fixed at
  creation until `workspace.set_mode` is built.
- All workspaces in one coordinator share its serial dispatch, so a
  hot one can slow a cold one; put busy workspaces on separate
  coordinators.

### 3.2 Membership policy

Decide who can join, who can be a Drafter, who can be a Reviewer,
and how Operators are appointed. Common shapes:

| Policy            | Means                                                  |
|-------------------|--------------------------------------------------------|
| Closed            | Operator explicitly admits each participant.           |
| Open within org   | Anyone with the right OIDC scope auto-joins.           |
| Federation        | Members of named partner workspaces auto-join via bridge participants. |

Neither coordinator controls admission yet: `participant.join` admits
any caller in the role it asks for. Until milestone 0.6, authenticate
joins and enforce role policy in front of the coordinator.

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
    deliberation:  required
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
customer?", closed-set, time-bound, with a default if no one
answers.

**If multiple humans must agree** → add `deliberation`. Quorum,
weighted votes, vetoes.

**If shifts change or work routes between humans** → add `handoff`.

**If you need a production control plane** (pause an agent, snapshot
a workspace, roll back the mode ceiling and members' roles and scopes)
→ add `control`.

**If non-repudiation matters** → add `security-signed` through the
`requireSignatures` option (`require_signatures` in Python);
advertising it at `workspace.create` without the option is refused,
and every call except `workspace.create` and `participant.join` then
needs a client signature.

**If regulatory audit matters** → add `audit-scitt`: the hash chain
switches on, and `audit.submit_to_scitt` prepares statements for a
transparency service you connect.

**If verified human identity matters** → add `identity-oidc` with
`verifyOidcToken`; `enforceStepUp` adds step-up.

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

### 5.1 Shadow (1-4 weeks)

The new agent processes real traffic, but its output is **not
delivered** to the end recipient. Your delivery layer sends it only to
shadow observers, who compare it against the live flow's output.
Withholding shadow output is the deployment's job: the Coordinator
checks a new task's mode against the ceiling and keeps no list of
shadow observers.

Promotion criteria (typical):

- Output matches live flow ≥ 95% per task kind.
- No protocol-level errors (no malformed envelopes, no illegal
  state transitions).
- Throughput meets the SLA.

### 5.2 Trial (1-2 weeks)

The agent's output **is** delivered to the recipient, but **every
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
The override-capture infrastructure stays on, you still want the
learning signal, but it covers a fraction of traffic.

### 5.4 Demoting

A regression in production override rate, an incident, or a policy
change can demote an agent back to `trial` or `shadow`. The
`control.set_mode_ceiling` operation records the change in the
audit log. New tasks above the lowered ceiling are refused with
`-32040`, including those that would take a production workspace's
own mode, so pass `mode: "trial"` on new tasks.

---

## 6. Capturing overrides as learning data

The single most valuable property of CHAP is that **every
override is structured data by construction**. This section
explains how to actually use that data.

### 6.1 What's in an override

An override carries four fields:

| Field         | Meaning                                                  |
|---------------|----------------------------------------------------------|
| `diff`        | RFC 6902 JSON Patch, the exact edit.                    |
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
    "filter":    { "method": "decide.override", "outcome": "accepted" }
  }
}
```

You get the accepted `decide.override` entries, each holding its call
under `envelope`. Filter dates on `arrived`, client-side; find the
originating agent through the task's `review.request` entry. Aggregate
by `tags`, by `from` (which reviewer) or by `policy_refs`.

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

With `enforceStepUp` (`enforce_step_up` in Python) on, step-up guards
the control methods, `workspace.set_profiles` and the two key methods
for humans and OIDC-bound members, using the `auth_time` of the token
last verified at `participant.join`. The pattern:

```
privileged call arrives
   │
   ▼
Coordinator checks the auth_time stored at participant.join
   │
   ├── within step_up_window_sec → proceed
   │
   └── stale → return -32402; the client signs in again (prompt=login)
               and repeats participant.join with the new token
```

Set `step_up_window_sec` (default 300) on `workspace.create`. A
`min_acr` set there also requires the token's `acr` to match.

### 7.4 Mapping OIDC scopes to CHAP roles

A reasonable starting map:

```yaml
oidc_scope_to_role:
  chap.read:    auditor
  chap.user:    [drafter, reviewer]
  chap.admin:   operator
  chap.audit:   auditor
```

Role-to-method permissions live in your deployment's policy. Example:

```yaml
role_permissions:
  auditor:    [audit.read]
  drafter:    [task.update, task.complete, notify.message]
  reviewer:   [decide.approve, decide.reject, decide.override, abstain.declare, escalate.raise]
  operator:   [control.*, workspace.*, participant.leave]
```

Neither coordinator controls admission yet: `participant.join` admits
any caller in the role it asks for, and the only role checks are the
admin role on `workspace.set_profiles` and on revoking another member's
key. Until milestone 0.6, authenticate joins and enforce role policy in
front of the coordinator.

### 7.5 What the Coordinator enforces before your policy runs

Two checks happen in the Coordinator itself, beneath whatever
role-to-method policy you configure above:

1. **Membership.** Membership (`-32011`) is checked for
   `task.create`, `task.update`, `task.complete`, the `review/1.0`
   methods, `workspace.set_profiles` and every control, deliberation,
   handoff and whisper method. The `routing/1.0` methods and
   `participant.leave` do not check it yet, so a non-member's routing
   call is accepted and recorded; `workspace.create`,
   `participant.join`, `audit.submit_to_scitt` and the reads are exempt
   by design. The `requireReadMembership` option adds the check to
   `audit.read` and `workspace.describe`; the rest is always on.
2. **Reviewer-set eligibility (with `review/1.0`).** A review decision
   (`decide.*`, `abstain.declare`) is accepted only from a member who was
   named in the review's `to` set. A member who was not asked to review
   cannot decide it. The decision `rule` controls how many of the
   addressed reviewers must act; the `to` set controls who is eligible.
   To let any member review, address the request to `workspace:<id>` or
   a `group:<id>`. Either address is a broadcast: it makes any workspace
   member eligible. A `group:` target does not narrow eligibility to the
   members of that named group.

These checks sit underneath your policy. Neither coordinator reads a
`role_permissions` map, so a rule such as "only `reviewer` may call
`decide.*`" is enforced in front of the coordinator until milestone
0.6. Even a participant your policy would allow must first be a
member, and for a review decision must first be an addressed reviewer.
Admitting someone new (an escalation target, an emergency approver) is
done by joining them first; a non-member cannot decide a review, so
the admission itself is always on the record. See SPECIFICATION.md §6.3.1
and profiles/review.md §3.2.

---

## 8. Audit, retention, and right-to-be-forgotten

### 8.1 What the audit log contains

Every accepted state-changing envelope, verbatim, in arrival order,
with the Coordinator's arrival timestamp. From 0.3.0 it also holds
every refused call that [SPECIFICATION §10.1](./SPECIFICATION.md)
records, under `request` with its outcome. Reads are not recorded.
With `security-signed`, the signatures are preserved. With
`audit-scitt` and a configured submitter, `audit.submit_to_scitt`
sends a statement for each entry to a transparency service, and the
receipts come back to the caller.

### 8.2 Retention

Retention is deployment policy, not protocol. Common settings:

| Workspace kind             | Typical retention      |
|----------------------------|-----------------------|
| Operational (support, ops) | 1-2 years             |
| Compliance-relevant        | 7 years               |
| Healthcare-regulated       | 10+ years per jurisdiction |
| Federal-finance            | Per regulation        |

### 8.3 Right-to-be-forgotten

Append-only logs and GDPR's erasure right are in conflict.
CHAP's recommended pattern:

1. **Don't store personal data in envelopes that don't need it.**
   Pseudonymise wherever possible: refer to a customer by an opaque
   id, not by name in the envelope body.
2. **Use a redaction registry.** Personal data that does end up
   in the log is referenced by a redaction key; when a subject
   exercises erasure rights, the key is rotated and the cleartext
   is removed from any side-store, leaving the envelope's hash
   intact but its referenced cleartext unrecoverable.
3. **Document the policy.** The descriptor schema's `policy_uri` is
   stored by neither coordinator, so publish your data-handling policy
   alongside the workspace, giving subject-rights requests a defined
   path.

[SPECIFICATION §10.6](./SPECIFICATION.md#106-retention-and-redaction)
specifies `audit.redact`, which is not yet built, so the mechanism is
the deployment's for now. See
[`SECURITY.md`](./SECURITY.md) §8, which records the absence of
confidentiality for evidence-chain content and recommends opaque
artefact URIs with external content storage.

### 8.4 Auditor access

An Auditor reads the log, never writes to it. With the chain on,
`audit.verify_chain` checks the hash links. Under `audit-scitt/1.0`,
receipts from the transparency service your deployment connects are
checked against that service, and `audit.verify_receipt` passes a
receipt to a verifier your deployment supplies. Neither coordinator
runs a transparency service.

---

## 9. Production deployment

[`integrations/CHAP-deployment-patterns.md`](./integrations/CHAP-deployment-patterns.md)
sketches wider patterns, some using transports and stores neither
coordinator ships. What the coordinators support today:

### 9.1 Topology

| Component                | What it does                                  | Typical implementation       |
|--------------------------|-----------------------------------------------|------------------------------|
| Coordinator              | Accepts envelopes, checks them, appends the audit log. | Stateful single writer: one active instance per workspace |
| Audit store              | Durable storage of workspace state and the log. | The SQLite store each package ships, or your own store |
| Identity provider        | OIDC tokens, key issuance.                    | Okta, Auth0, Keycloak, etc.  |
| SCITT service (optional) | Transparency log + receipts.                  | A SCITT-compliant service, connected through the submitter option |
| Participant clients      | Humans (UI), agents (libraries), services.    | Implementation-specific      |

### 9.2 Transport

| Transport                           | Status                                              |
|-------------------------------------|-----------------------------------------------------|
| HTTP POST                           | Required by Core; both reference servers speak it.  |
| MCP, A2A                            | Through the adapter packages.                       |
| WebSocket, HTTP + SSE, Kafka / NATS | Specified in SPECIFICATION.md §14; unbuilt.         |

Neither reference coordinator pushes. A UI polls `audit.read`, passing
`range.from_seq` as the last `next_seq` it received; milestone 0.5
decides on notifications.

### 9.3 Sizing

No benchmark is published yet; milestone 0.4 adds one to every
release. Size from measurements of your own workload.

### 9.4 High availability

The Coordinator is a single writer that holds state in memory; two
instances writing one workspace lose entries silently
([SPECIFICATION.md §10.3](./SPECIFICATION.md#103-single-writer-requirement)).
Run one active instance per workspace, partition workspaces or lock
writes externally, and start a standby from the store on failover.
Replicate the store, and keep each workspace in one region; federate
across regions through the bridge pattern.

---

## 10. Monitoring and observability

A useful CHAP deployment publishes:

| Metric                                          | Source                              |
|-------------------------------------------------|-------------------------------------|
| Envelopes/sec by method                         | Coordinator                         |
| p50/p95/p99 envelope handling latency           | Coordinator                         |
| Override rate by task kind                       | Audit query                         |
| Abstention rate by participant                   | Audit query                         |
| Time-to-review (review.request → decide.*)       | Audit query                         |
| Mode ceiling changes                             | `control.set_mode_ceiling` entries  |
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
    "workspace":       "wsp_support_triage",
    "from":            "human:oncall@example.org",
    "scope":           "participant",
    "participant_uri": "agent:triage-bot#v3.3",
    "reason":          "Override-rate spike: 38% in last 15 minutes.",
    "in_flight_policy": "allow_to_complete"
  }
}
```

New tasks assigned to the agent are refused with `-32063` from now
on. Tasks already in flight carry on: the coordinator echoes
`in_flight_policy` and leaves them alone, so nothing is abandoned
half-done.

### 11.2 Demote the mode

```json
{
  "method": "control.set_mode_ceiling",
  "params": {
    "workspace":   "wsp_support_triage",
    "from":        "human:oncall@example.org",
    "new_ceiling": "trial"
  }
}
```

New tasks above `trial` are refused with `-32040`, including those
that would take a production workspace's own mode, so pass
`mode: "trial"`. Under `modes/1.0`, a trial task requires review.

### 11.3 Snapshot, investigate, decide

```json
{
  "method": "control.snapshot",
  "params": {
    "workspace": "wsp_support_triage",
    "from":      "human:oncall@example.org",
    "label":     "pre-rollback-investigation"
  }
}
```

Use the snapshot to gather state for the incident review. Its
`audit_seq` marks where it was taken: pass it to `audit.read` as
`range.from_seq` to see everything since, and query for the
symptomatic methods.

### 11.4 Roll back if appropriate

If a misconfiguration is the cause, `control.rollback` restores the
mode ceiling and members' roles and scopes from the named snapshot, and
is appended to the log.

### 11.5 Reactivate

Raise the ceiling again when the fix is verified:

```json
{
  "method": "control.set_mode_ceiling",
  "params": {
    "workspace":   "wsp_support_triage",
    "from":        "human:oncall@example.org",
    "new_ceiling": "production"
  }
}
```

And resume:

```json
{
  "method": "control.resume",
  "params": {
    "workspace":       "wsp_support_triage",
    "from":            "human:oncall@example.org",
    "scope":           "participant",
    "participant_uri": "agent:triage-bot#v3.3"
  }
}
```

The full incident is reconstructible from the audit log: when the
pause happened, who issued it, what was rolled back, and when normal
operations resumed.

---

## 12. Common patterns

### 12.1 Drafter-Reviewer

The classic. An agent drafts; a human approves or overrides.

```
human creates task → agent drafts → review.request → human decides → done
```

Use `review` + (optionally) `modes`. See [`examples/03-review-and-approve.md`](./examples/03-review-and-approve.md).

### 12.2 Drafter-Reviewer-Approver

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
internal concern. See [`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

### 12.7 Tool-using agent with MCP

```
human creates task → agent calls MCP tool → result cited in artefact → review.request → done
```

See [`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md).

---

## 13. Anti-patterns

Things CHAP doesn't stop you from doing but you shouldn't.

**One huge workspace for everything.** Per §3.1, narrow workspaces
beat wide ones. Audit queries get slow, policy gets tangled, mode
promotion affects unrelated work.

**Skipping `review` on agent output for "small" tasks.** Without
`review`, you have no override-capture signal. Even if you don't
need approval, the structured-edit data is the point.

**Free-text answers in `whisper`.** Defeats aggregation. Always
provide a closed option set unless the question needs free text.

**Replacing the audit log when GDPR erasure is requested.** Use the
redaction-key mechanism (see [§8.3](#83-right-to-be-forgotten)).
Truncating the log breaks every signature chain and every SCITT
receipt downstream.

**Treating `mode_ceiling` as configuration.** It is set by
`control.set_mode_ceiling`, a privileged call that is recorded, and
step-up guards it where the coordinator enforces step-up. Don't read
it from an unsigned config file.

**Per-participant audit logs.** The log is workspace-scoped. A
participant doesn't have its own log; it has its messages in
others' logs. (To get a participant-centric view, query the
workspace log with `filter.from = participant_uri`.)

**Custom URI schemes inside the protocol.** Reuse the five CHAP URI
schemes (`human:`, `agent:`, `service:`, `group:`, `workspace:`)
plus DNS or DID authorities. Inventing your own breaks interop.

**Encapsulating MCP or A2A traffic in CHAP envelopes.** Cite them;
don't copy them. The CHAP audit log links to MCP transcripts, it
doesn't contain them.

---

## Where to next

- For envelope-level detail: [`core/SPEC.md`](./core/SPEC.md) and
  the relevant profile in [`profiles/`](./profiles/).
- For end-to-end worked scenarios: [`examples/`](./examples/).
- For composition with adjacent protocols: [`integrations/`](./integrations/).
- For frequently asked questions: [`FAQ.md`](./FAQ.md).
