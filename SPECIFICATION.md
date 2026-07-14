# Collaborative Human-Agent Protocol (CHAP): Specification

**Audience:** Implementers · **Format:** Combined Core + Profiles reference

---

> ### Orientation
>
> This document is the **combined reference**: Core and every
> profile in a single document, cross-referenced and ready for
> implementers who want one place to look up every detail.
>
> For most newcomers, the right entry points are:
>
> - **[README](./README.md)**: overview and reading paths.
> - **[Handbook](./HANDBOOK.md)**: practical operator's manual.
> - **[`core/SPEC.md`](./core/SPEC.md)**: minimal Core specification (weekend-implementable).
> - **[`profiles/PROFILES.md`](./profiles/PROFILES.md)**: profile catalogue.
>
> This document compiles all of the above. It is normative for the
> protocol as a whole; the individual Core and profile documents are
> normative for their respective parts and link back here.

---

## Status of this document

This document specifies the Collaborative Human-Agent Protocol. The keywords **MUST**,
**MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**,
**SHOULD NOT**, **RECOMMENDED**, **MAY**, and **OPTIONAL** are to be
interpreted as described in [RFC 2119] and [RFC 8174] when,
and only when, they appear in all capitals.

[RFC 2119]: https://www.rfc-editor.org/rfc/rfc2119
[RFC 8174]: https://www.rfc-editor.org/rfc/rfc8174

**Maturity.** CHAP 0.2 is a public **Draft**. The protocol surface,
schemas, and reference implementations are stable enough for
experimentation and early production pilots; they are not yet
sufficient for a normative conformance claim. Specifically: the
specification has one reference implementation (the `@brightbeamai/coordinator`
package in this repository), not the two interoperable implementations
typical of a standards-track promotion; an empirical interoperability
test suite covering all defined methods is published as a draft (see
[`conformance/`](./conformance/)) but is not exhaustive. Breaking
changes to the wire format will follow Semantic Versioning, but the
profile surface should be expected to evolve faster than Core.
Production deployments are welcome and encouraged to feed back
findings; deployments requiring stability guarantees beyond
"reasonable best effort under SemVer" should wait for 1.0.

---

## Table of contents

1. [Introduction](#1-introduction)
2. [Terminology](#2-terminology)
3. [Protocol stack and positioning](#3-protocol-stack-and-positioning)
4. [Wire format](#4-wire-format)
5. [Identity and signing](#5-identity-and-signing)
6. [Workspaces](#6-workspaces)
7. [Participants](#7-participants)
8. [Tasks](#8-tasks)
9. [Artefacts](#9-artefacts)
10. [Evidence and audit](#10-evidence-and-audit)
11. [Modes](#11-modes)
12. [Methods](#12-methods)
13. [Error model](#13-error-model)
14. [Transports](#14-transports)
15. [Security considerations](#15-security-considerations)
16. [Composition with MCP and A2A](#16-composition-with-mcp-and-a2a)
17. [Conformance](#17-conformance)
18. [IANA considerations](#18-iana-considerations)

---

## 1. Introduction

### 1.1 Motivation

Modern software increasingly involves AI agents producing work that humans
must review, approve, override, or escalate. Today, every team building such
systems re-invents the same primitives:

- A queue for handing work between humans and agents.
- An approval-and-override interface.
- A custom audit log.
- A different identity story for each integration.
- A fragile bridge between agent tool calls (MCP) and human approval.

These ad-hoc layers do not interoperate, do not compose with existing
standards, and produce audit logs whose integrity is hard to verify after
the fact. CHAP standardises this layer.

### 1.2 Design goals

1. **Symmetric peer model.** Humans and agents are both Participants;
   they differ in capability profile, not protocol standing.
2. **Workspace as the unit of collaboration.** Every interaction happens
   inside a named workspace with explicit membership and policy.
3. **Evidence-first.** Every message is signed; every signed message
   extends a hash-chained log; audit is a first-class operation.
4. **Mode-aware.** `shadow`, `trial`, and `production` are envelope-level
   concerns enforced by the Coordinator.
5. **Transport-agnostic.** The semantics are identical over WebSocket,
   HTTP+SSE, polling, NATS, Kafka, or RabbitMQ.
6. **Composable with MCP and A2A.** CHAP cites tool calls and cross-system
   agent messages inside its own evidence chain.
7. **Boring on purpose.** JSON-RPC-2.0-style envelope, JSON Schema for
   every primitive, Ed25519 + JCS for signing.

### 1.3 Non-goals

CHAP is not:

- A user interface specification. It defines a wire format and a method
  catalogue, not the shape of an approval dialog.
- A workflow engine. CHAP carries the messages a workflow engine
  produces; it does not itself execute long-running business logic.
- A replacement for MCP or A2A. It composes with both.
- A confidentiality layer for sensitive payloads. Use opaque
  artefact references and external content storage.
- A business-process notation. Mechanism, not policy.

CHAP also deliberately leaves the following to deployments and profiles:

- **A claim or evidence taxonomy.** CHAP carries an artefact's
  `content` and `citations` opaquely. Whether claims are typed as
  Evidence/Inference/Assumption, as Premise/Conclusion, or as some
  domain-specific scheme is the deploying organisation's choice or a
  profile's contribution.
- **A temporal model beyond `produced_at` and chain monotonicity.**
  Domains that require richer time semantics, separating subject
  time from statement time, or carrying validity windows, should
  layer those into the artefact `content` shape or define them in a
  profile.
- **A confidence calibration.** `routing_hints.confidence` is a
  model-reported number. CHAP makes no claim about cross-model
  comparability or about what any particular value implies for
  routing.
- **What evidence is sufficient for any regulatory regime.** CHAP
  produces a verifiable record of who decided what, when, and on the
  basis of which inputs. Whether that record meets a particular
  audit, conformity assessment, or accountability standard is for
  the deploying organisation and its regulators to determine.
- **Semantic relations between artefacts beyond `based_on` and
  supersession.** Richer graphs (causation, mitigation, verification)
  belong in domain layers above CHAP.

---

## 2. Terminology

This section defines terms used normatively throughout the document. See
[GLOSSARY.md](./GLOSSARY.md) for an extended glossary with adjacent terms.

- **Workspace.** A named, addressable collaboration context with a
  membership list, a policy, a mode, and an append-only evidence log.
- **Participant.** Any entity that can send or receive CHAP messages
  inside a workspace. Participants are typed as `human`, `agent`,
  `service`, `group`, or `workspace`.
- **Coordinator.** The component that mediates a workspace: routes
  messages, enforces policy and mode, and appends entries to the
  evidence chain. The Coordinator is a Participant of type `service`.
- **Task.** A unit of work proposed, accepted, performed, and resolved
  inside a workspace. Tasks have a lifecycle and produce artefacts.
- **Artefact.** A typed payload produced by a Participant in the
  course of a task, a draft, a decision, an override, a citation set,
  a structured record.
- **Override.** An artefact that records a human's modification of an
  agent's output, including the diff, rationale, and applicable tags.
- **Evidence entry.** A signed, hash-linked record of a single CHAP
  message inside a workspace's evidence log.
- **Mode.** The operational regime of a workspace or a specific task:
  `shadow`, `trial`, or `production`.

---

## 3. Protocol stack and positioning

CHAP sits alongside MCP and A2A as the third layer of the agent-protocol
stack. The three protocols address disjoint concerns:

| Protocol | Concern                          | Primary endpoints      |
|----------|----------------------------------|------------------------|
| **MCP**  | An agent calling a tool          | Agent ↔ Tool server    |
| **A2A**  | Agents talking across systems    | Agent ↔ Agent          |
| **CHAP**  | The shared collaboration room    | Human ↔ Agent ↔ Human  |

A typical deployment looks like:

```
┌────────────────────────────────────────────────────────────────────┐
│                          CHAP Workspace                              │
│  (humans, agents, services as peers; one evidence chain)            │
│                                                                     │
│   human ──┐                       ┌── agent ──[ MCP ]── tool        │
│           ├─ CHAP ─ Coordinator ─┤                                   │
│   human ──┘                       └── agent ──[ A2A ]── peer        │
└────────────────────────────────────────────────────────────────────┘
```

When an agent calls a tool over MCP, the call and its result are cited
inside the CHAP evidence chain so that a single audit covers the full
human-agent-tool path. When an agent delegates work to a peer in another
organisation over A2A, a CHAP bridge participant represents the remote
work in the local workspace.

---

## 4. Wire format

### 4.1 Envelope

Every CHAP message is a JSON object conforming to
[`schemas/chap-envelope.schema.json`](./schemas/core/chap-envelope.schema.json).

```json
{
  "chap": "0.2",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2A",
  "ts": "2026-05-17T09:14:22.184Z",
  "workspace": "wsp_support_triage",
  "from": "human:alice@example.org",
  "to":   "agent:triage-bot#v3.2",
  "type": "request",
  "method": "task.assign",
  "params": { /* method-specific */ },
  "evidence": {
    "prev_hash": "sha256:5f1c4e9b7a8d2f3e1c0a9b8d7e6f5c4b3a29180716054433221100ffeeddccbb",
    "sig": "ed25519:V8M2cQ7K3X8M2V4N6P8R0T2AV8M2cQ7K3X8M2V4N6P8R0T2AV8M2cQ7K3X8M2V4N6P8R0T2AV8M2cQ7K3X8M2V4N6P8R0T2Aq0kg=="
  }
}
```

Field-by-field:

| Field       | Type            | Required | Description                                                                 |
|-------------|-----------------|----------|-----------------------------------------------------------------------------|
| `chap`       | string (SemVer) | yes      | Wire version. Implementations MUST refuse unrecognised major versions.      |
| `id`        | string (ULID)   | yes      | Globally unique message identifier. MUST be a ULID (Crockford-base32, 26 chars). |
| `ts`        | string (RFC3339) | yes     | UTC timestamp with millisecond precision. MUST be monotonic per `from`.     |
| `workspace` | string          | yes      | Workspace identifier, prefix `wsp_`.                                        |
| `from`      | Participant URI | yes      | The originator.                                                             |
| `to`        | Participant URI \| string[] | yes | Recipient or recipients. Use `workspace:wsp_…` for broadcast.        |
| `type`      | enum            | yes      | One of `request`, `response`, `notification`.                               |
| `method`    | string          | conditional | Required for `request` and `notification`. Of the form `namespace.verb`. |
| `params`    | object          | conditional | Required for `request` and `notification`.                              |
| `result`    | any             | conditional | Required for successful `response`.                                     |
| `error`     | Error object    | conditional | Required for failed `response`.                                         |
| `evidence`  | object          | yes      | Hash chain pointer and signature; see §10 and §5.                           |

### 4.2 Message types

CHAP uses a JSON-RPC-2.0-inspired but not identical three-type model:

- **`request`**: solicits a response. Carries `method` and `params`.
  The Coordinator MAY answer requests directly (e.g. for routing or
  policy queries) but typically forwards to the addressed Participant,
  which replies with a `response` whose `id` echoes the request's `id`.
- **`response`**: answers a previous `request`. Carries `result` on
  success or `error` on failure. The `id` MUST match the request's `id`.
- **`notification`**: fire-and-forget. Carries `method` and `params`.
  No response is expected. Used for status updates, progress reports,
  and pub-sub events.

### 4.3 ID and timestamp constraints

The `id` field MUST be a [ULID](https://github.com/ulid/spec): 26
Crockford-base32 characters. ULIDs encode their creation time in their
prefix, which gives sortability and makes accidental reuse detectable.

The `ts` field MUST be UTC with millisecond precision and MUST be
strictly monotonic for messages from the same `from` Participant.
Implementations encountering a non-monotonic timestamp from the same
origin MUST reject the message with error code `-32401` (`temporal_order_violation`).

### 4.4 Size limits

Conformant implementations MUST accept envelopes up to **1 MiB**. They
MAY accept larger envelopes but SHOULD prefer to reference large
artefact content by URI rather than inlining it. Coordinators MUST
publish their configured maximum in the workspace descriptor.

---

## 5. Identity and signing

### 5.1 Participant URI scheme

Participant identifiers are URIs with five reserved schemes:

```
human:<local-id>[@<authority>]
agent:<name>[@<authority>][#<version>]
service:<name>[@<authority>]
group:<name>[@<authority>]
workspace:<workspace-id>
```

Examples:

- `human:alice@example.org`
- `human:reviewer-7@hospital.example.com`
- `agent:triage-bot#v3.2`
- `agent:code-reviewer@example.org#v1.0`
- `service:coordinator@example.org`
- `group:on-call-engineers@example.org`
- `workspace:wsp_support_triage`

The `@authority` portion identifies the issuing identity domain.
The `#version` portion is OPTIONAL and identifies a specific agent
build. Two URIs that differ only in `#version` are different
Participants for the purposes of authorisation but MAY be aliased
in human-readable UI.

### 5.2 Signing algorithm

Every CHAP message MUST be signed. The signature algorithm is
**Ed25519** ([RFC 8032]). The signed input is the
**JCS canonicalisation** ([RFC 8785]) of the envelope **with the
`evidence.sig` field removed** but `evidence.prev_hash` retained.

[RFC 8032]: https://www.rfc-editor.org/rfc/rfc8032
[RFC 8785]: https://www.rfc-editor.org/rfc/rfc8785

**Canonical number restriction.** RFC 8785 §3.2.2.3 specifies number
serialisation via the ECMAScript number-to-string algorithm. Reproducing
that algorithm byte-identically across languages is error-prone, and any
mismatch would cause a chain or signature produced by one implementation
to fail verification against another. To make cross-implementation
agreement provable rather than approximate, CHAP restricts the canonical
number space: a number in a CHAP envelope or artefact MUST be an integer
whose absolute value is at most 2^53 - 1 (the ECMAScript safe-integer
bound). Non-integer values and integers of larger magnitude are not valid
CHAP canonical numbers and MUST be represented as strings (for example the
decimal reading `"8.2"`, or the digits of a large identifier). A JSON
literal such as `2.0` is integer-valued and canonicalises to `2`.
Conforming implementations MUST reject out-of-range and non-integer
numbers identically; the shared vectors in
`conformance/canonical-number-vectors.json` pin the accepted outputs and
the rejected inputs. A future protocol version MAY define a canonical
decimal-string format to admit fractional values without ambiguity.

Signing procedure:

1. Construct the envelope as a JSON object.
2. Set `evidence.prev_hash` to the SHA-256 of the previous evidence
   entry in this workspace (see §10).
3. Remove `evidence.sig` from the object (or set to `null`).
4. Canonicalise per JCS.
5. Sign the canonical bytes with the Participant's Ed25519 private key.
6. Set `evidence.sig` to `ed25519:<base64-encoded-signature>`.

Verification reverses the procedure. The verifier MUST look up the
public key for the claimed `from` Participant *as of the message's
`ts`*, keys may have rotated since.

### 5.3 Key formats

Public keys are represented as JWKs ([RFC 7517]) with `kty: "OKP"`,
`crv: "Ed25519"`. They are advertised either:

- In the workspace's participant descriptor (`participant.describe` result), or
- Via a JWKS endpoint referenced by the participant descriptor's
  `jwks_uri` field.

[RFC 7517]: https://www.rfc-editor.org/rfc/rfc7517

Keys carry a `kid` (key ID). The `evidence.sig` field MAY be prefixed
with a key ID hint: `ed25519:<kid>:<base64-signature>`.

### 5.4 Human identity binding

Human participants SHOULD use **ephemeral signing keys** bound to an
OIDC ID token. The binding follows DPoP ([RFC 9449]) in spirit:

1. The client generates an Ed25519 keypair at session start.
2. The client requests an OIDC ID token carrying a `cnf.jwk` claim
   whose value is the public key.
3. The Coordinator, on receiving the first message of the session,
   verifies the ID token, extracts the `cnf.jwk`, and pins it as the
   signing key for this human Participant for the session's lifetime.
4. The Coordinator MAY require periodic re-binding (token refresh +
   key rotation) for long-lived sessions.

[RFC 9449]: https://www.rfc-editor.org/rfc/rfc9449

This pattern guarantees that:

- A leaked long-term password cannot be replayed against CHAP.
- A leaked ephemeral key is useful only for the OIDC session's
  remaining lifetime.
- The audit chain ties every signed action to a specific
  authentication event, addressable by `auth_time` and `acr`.

### 5.5 Agent and service identity

Agents and services SHOULD use workload identities. Recommended
options, in order of preference:

1. **SPIFFE SVIDs** for service mesh deployments.
2. **mTLS** with X.509 certificates issued by an internal CA.
3. **OIDC client credentials** with a bound JWK.

In every case, the signing key for CHAP messages is bound to the
workload identity. Long-lived agent identifiers (like
`agent:triage-bot`) MAY map to a sequence of short-lived keys; the
mapping is published in the participant descriptor.

### 5.6 Step-up authentication

Methods marked `privileged: true` in the method catalogue
(see §12 and [`schemas/chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json))
require step-up authentication. The Coordinator MUST verify that the
caller's most recent OIDC `auth_time` is within the configured
step-up window (default: 5 minutes). The step-up window is published
in the workspace descriptor.

### 5.7 Key rotation

Participants rotate keys with `participant.rotate_key`. The request
includes the new public key (as a JWK) and is signed with the *old*
key. After acceptance:

- Messages from the old key are accepted for verification of historical
  evidence indefinitely.
- New messages from the old key are accepted for a grace window
  (default: 5 minutes) and rejected thereafter.

Compromised keys are revoked with `participant.revoke_key`, signed by
an admin participant. Revocation is recorded in the evidence chain.

---

## 6. Workspaces

### 6.1 Lifecycle

A workspace is created with `workspace.create`. The creator becomes
the initial admin. Workspaces have an explicit lifecycle:

```
created → active → (paused ↔ active)* → closed → archived
```

`paused` workspaces accept no new tasks but may accept administrative
operations. `closed` workspaces accept no new operations of any kind;
their evidence chain is sealed. `archived` workspaces are read-only.

### 6.2 Descriptor

`workspace.describe` returns a descriptor conforming to
[`schemas/chap-workspace.schema.json`](./schemas/core/chap-workspace.schema.json):

```json
{
  "id": "wsp_support_triage",
  "name": "Customer support triage",
  "created": "2026-05-01T09:00:00Z",
  "state": "active",
  "mode": "production",
  "mode_ceiling": "production",
  "step_up_window_sec": 300,
  "max_envelope_bytes": 1048576,
  "coordinator": "service:coordinator@example.org",
  "policy_uri": "https://example.org/policies/support-triage.json",
  "members": [
    { "uri": "human:alice@example.org", "role": "reviewer" },
    { "uri": "human:bob@example.org",   "role": "approver" },
    { "uri": "agent:triage-bot#v3.2",   "role": "drafter" },
    { "uri": "service:coordinator@example.org", "role": "coordinator" }
  ],
  "shadow_observers": ["human:eve@example.org"],
  "evidence_head": "sha256:8b1c…d9e0",
  "evidence_count": 14823
}
```

### 6.3 Membership and roles

Roles are workspace-local strings. The protocol defines two
**reserved** role names:

- **`coordinator`**: exactly one Participant per workspace, of
  type `service`. Holds routing and mode-enforcement authority.
- **`admin`**: one or more Participants, of type `human` or
  `service`. May invite, evict, set mode, and rotate Coordinator
  responsibilities.

All other role names are deployment-defined. The workspace's
`policy_uri` describes which roles may invoke which methods.

#### 6.3.1 Actor membership (precondition)

The `from` field of every method names the **actor**: the Participant
on whose behalf the envelope is sent. For every method other than
`participant.join` itself, the actor MUST be a current member of the
named workspace at the time the envelope is processed. A Coordinator
MUST reject an envelope whose `from` is not a joined member. The error
table (§13.3) names this condition `unknown_participant`; the reference
implementations currently surface it with the `not_authorised` code
(-32011) rather than the table's -32403, because -32403 already denotes
an invalid OIDC token in their private error range. This code-level
divergence is being reconciled separately and does not affect the
precondition itself. Enforcing it makes the audit log's attribution
sound: a recorded decision, completion, or review request can never
name a Participant who never joined.

Membership is the floor, not the ceiling. Individual profiles MAY
impose a stricter eligibility rule on top of it. In particular, the
`review/1.0` profile requires that the actor of a review decision
(`decide.approve`, `decide.reject`, `decide.override`, `abstain.declare`)
be one of the reviewers the review was addressed to in `review.request`'s
`to` set; see [`profiles/review.md`](./profiles/review.md). Membership
verification is distinct from, and composes with, identity verification:
the `identity-oidc/1.0` and `identity-vc/1.0` profiles bind a verified
real-world identity to a Participant, but the membership precondition
here applies whether or not those profiles are in force.

Legitimately admitting a new actor (an escalation target, or an
emergency "break-glass" approver) is done by joining them first, which
records the entry into the workspace as its own audit event. There is
no path by which a non-member acts; the exceptional nature of an
admission is captured in how, and under what role, the Participant
joined.

### 6.4 Policy

A workspace's policy describes:

- The mapping from role to allowed methods (the **method-role matrix**).
- The mode ceiling and promotion rules (§11).
- The step-up authentication window.
- The retention policy for evidence and artefacts.
- The list of permitted MCP servers and A2A peers.

Policy is referenced by URI; the policy itself is out of scope for
this specification. The policy document SHOULD be a signed JSON object
fetchable over HTTPS, with a hash committed to the workspace descriptor
at creation time.

---

## 7. Participants

### 7.1 Descriptor

Every Participant has a descriptor obtainable via `participant.describe`,
conforming to [`schemas/chap-participant.schema.json`](./schemas/core/chap-participant.schema.json):

```json
{
  "uri": "agent:triage-bot#v3.2",
  "type": "agent",
  "display_name": "Support Triage Bot",
  "version": "3.2.0",
  "jwks": {
    "keys": [
      { "kty": "OKP", "crv": "Ed25519", "kid": "k-2026-05",
        "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo" }
    ]
  },
  "capabilities": {
    "kinds": ["draft_response", "classify", "extract_entities"],
    "modes": ["shadow", "trial", "production"],
    "max_concurrent": 32,
    "avg_latency_ms": 480
  },
  "scopes": ["task.accept", "task.complete", "review.request"],
  "supported_methods": [
    "task.accept", "task.complete", "review.request",
    "whisper.ask", "abstain.declare"
  ],
  "mcp_servers": [
    { "uri": "mcp+https://tools.example.org/orders", "name": "order-lookup" }
  ]
}
```

### 7.2 Capability profile

The `capabilities` block describes what the Participant is good at
and at what rate. This is **descriptive, not prescriptive**: the
Coordinator uses it for routing hints and load shaping; the
Participant's own authority is unchanged by claiming any particular
capability.

Capability fields:

| Field             | Type            | Description                                              |
|-------------------|-----------------|----------------------------------------------------------|
| `kinds`           | string[]        | Task kinds the Participant can perform.                  |
| `modes`           | enum[]          | Modes the Participant may operate in.                    |
| `max_concurrent`  | integer         | Maximum simultaneous tasks. Coordinator throttles above. |
| `avg_latency_ms`  | integer         | Expected time-to-first-response.                         |
| `confidence_calibration` | object   | (Agents only) self-reported calibration metrics.         |
| `tool_inventory`  | string[]        | (Agents only) MCP tools the Participant has access to.   |
| `working_hours`   | object          | (Humans only) availability window for routing.           |

### 7.3 Scopes

A Participant's `scopes` field declares the methods it is *willing*
to handle. Authority is determined by the workspace's policy
(method-role matrix), not by the Participant's claim. A Participant
that has not declared a scope is not eligible to receive that method.

---

## 8. Tasks

### 8.1 Lifecycle

A Task moves through a defined state machine:

```
created → assigned → accepted → in_progress → (review_requested) → completed
                  ↘  declined
                  ↘  abstained
                  ↘  escalated
                  ↘  cancelled
                  ↘  superseded
```

Terminal states: `completed`, `cancelled`, `superseded`. Non-terminal
states are reversible via `control.*` operations subject to policy.

Transitions are triggered by methods:

| From               | Method                        | To                |
|--------------------|-------------------------------|-------------------|
| created            | `task.assign`                 | assigned          |
| assigned           | `task.accept`                 | accepted          |
| assigned           | `task.decline`                | declined          |
| accepted           | `task.start`                  | in_progress       |
| in_progress        | `task.complete`               | completed         |
| in_progress        | `review.request`              | review_requested  |
| review_requested   | `decide.approve`              | completed         |
| review_requested   | `decide.reject`               | (back to in_progress or declined per policy) |
| review_requested   | `decide.override`             | completed (with override artefact) |
| any non-terminal   | `abstain.declare`             | abstained         |
| any non-terminal   | `escalate.raise`              | escalated         |
| any non-terminal   | `control.cancel`              | cancelled         |
| any state          | `control.supersede`           | superseded        |

### 8.2 Task descriptor

A Task conforms to [`schemas/chap-task.schema.json`](./schemas/core/chap-task.schema.json):

```json
{
  "id": "tsk_01HZ9YWQ7K3X8M2V4N6P8R0T3B",
  "workspace": "wsp_support_triage",
  "kind": "draft_response",
  "state": "in_progress",
  "mode": "production",
  "assignee": "agent:triage-bot#v3.2",
  "delegator": "human:alice@example.org",
  "input": {
    "ticket_id": "INC-48219",
    "customer_message": "My order hasn't arrived after 10 days."
  },
  "constraints": {
    "deadline": "2026-05-17T09:30:00Z",
    "max_tool_calls": 10,
    "permitted_tools": ["order-lookup", "shipping-status"]
  },
  "review": {
    "required": true,
    "reviewers": ["human:alice@example.org", "human:bob@example.org"],
    "rule": "any_one_approves"
  },
  "artefacts": ["art_01HZ9YX…"],
  "created": "2026-05-17T09:14:22.184Z",
  "updated": "2026-05-17T09:14:56.012Z"
}
```

### 8.3 Review rules

The `review.rule` field defines the predicate for moving from
`review_requested` to `completed`:

- `any_one_approves`: first `decide.approve` wins.
- `all_approve`: every named reviewer must approve.
- `quorum:<n>`: `n` approvals required.
- `weighted_vote:<threshold>`: weighted approvals summing to
  threshold (weights in workspace policy).
- `weighted_vote_with_veto:<threshold>`: as above, but any
  `decide.reject` from a reviewer with `veto: true` ends the review
  immediately as rejected.

### 8.4 Routing hints (optional)

A Task MAY carry an optional `routing_hints` object that captures
business-runtime signals: criticality tier, deadline, maximum cost,
risk classification. CHAP defines the field shape and signs the
values into the evidence envelope hash; it assigns the values no
semantics.

```json
{
  "routing_hints": {
    "criticality": "high",
    "deadline": "2026-05-17T17:00:00Z",
    "max_cost_usd": 50.00,
    "risk_tier": "financial-tier-2"
  }
}
```

Fields:

| Field          | Type    | Constraint                                        |
|----------------|---------|---------------------------------------------------|
| `criticality`  | string  | one of `low`, `medium`, `high`, `critical`        |
| `deadline`     | string  | RFC 3339 timestamp; when the work is needed       |
| `max_cost_usd` | number  | non-negative                                      |
| `risk_tier`    | string  | opaque to CHAP; org-specific                      |

Additional operator-defined fields are permitted. CHAP signs whatever
is present but interprets nothing, interpretation is the operator's
responsibility, and the `routing/1.0` profile defines methods that
consume the hints (`task.route`, `review.depth`, `escalate.auto`).

A Core-only implementation MUST forward `routing_hints` unchanged
when relaying messages. It MUST NOT discard hints it does not
understand.

---

## 9. Artefacts

### 9.1 Purpose

An Artefact is a typed payload produced inside a workspace, a draft
to be reviewed, a final decision, an override record, a structured
extraction, a citation set. Artefacts are first-class evidence: their
existence and content are recorded in the chain.

### 9.2 Descriptor

```json
{
  "id": "art_01HZ9YX1A2B3C4D5E6F7G8H9J0",
  "kind": "draft_response",
  "produced_by": "agent:triage-bot#v3.2",
  "produced_at": "2026-05-17T09:14:55.901Z",
  "task": "tsk_01HZ9YWQ7K3X8M2V4N6P8R0T3B",
  "schema": "https://schemas.example.org/draft-response.v1.json",
  "logical_id": "lgl_01HZ9YX1A2B3C4D5E6F7G8H9J0",
  "instance_id": "art_01HZ9YX1A2B3C4D5E6F7G8H9J0",
  "content": {
    "text": "Hello   thank you for reaching out about order #...",
    "tone": "apologetic",
    "compensation_offered": null
  },
  "citations": [
    {
      "kind": "mcp_tool_invocation",
      "server": "mcp+https://tools.example.org/orders",
      "tool": "lookup_order",
      "call_id": "call_01HZ9YX0…",
      "input_hash": "sha256:b2c3…",
      "output_hash": "sha256:d4e5…"
    }
  ],
  "confidence": 0.86,
  "content_hash": "sha256:7f8e9d0c…"
}
```

#### 9.2.1 Artefact identity: `id`, `logical_id`, `instance_id`

CHAP distinguishes three identity concepts on an artefact:

- **`id`** (required) is a globally unique handle for this particular
  artefact record. Each new artefact gets a fresh `id`.
- **`logical_id`** (OPTIONAL) names the *thing the artefact is about*
 , the durable handle that survives revision. Two artefacts that
  share a `logical_id` are two versions of the same underlying item:
  the same draft response, the same policy statement, the same
  recommendation. Producers SHOULD assign a `logical_id` on first
  creation and reuse it on every subsequent revision.
- **`instance_id`** (OPTIONAL) is a stable handle for the specific
  *version*. When present, an `instance_id` MUST equal the artefact's
  `content_hash` or be a function of it; this lets consumers detect
  whether two artefacts with the same `logical_id` are byte-identical.
  Implementations that do not need a separate instance handle MAY
  set `instance_id` equal to `id`.

These fields exist so that revision, supersession, and override can
be distinguished in the chain. Without them, a deployment can track
*which artefact replaced which* (via `based_on` and `control.supersede`)
but cannot answer *"is this the same item I approved last week, or a
different item with the same shape?"*, a question that arises in any
domain that does versioned work.

CHAP itself reads only `id`. Higher layers, analytics, dashboards,
external indexes, can use `logical_id` and `instance_id` to project
the chain into a version graph.

### 9.3 Standard artefact kinds

The specification defines a small set of standard kinds:

| Kind                | Produced by | Purpose                                              |
|---------------------|-------------|------------------------------------------------------|
| `draft`             | any         | Pre-review content of any sort.                      |
| `decision`          | any         | An approve/reject/override outcome.                  |
| `override`          | human       | A human's modification of an agent's draft.          |
| `abstention`        | any         | A record of declining to decide.                     |
| `escalation`        | any         | A handoff up the chain with context.                 |
| `citation_set`      | any         | A bundle of supporting references.                   |
| `snapshot`          | service     | A serialised workspace state for replay or rollback. |
| `capture_fragment`  | any         | An ad-hoc record produced via `capture.append`.      |

Implementations MAY define additional kinds; the `schema` field MUST
reference a published JSON Schema for any non-standard kind.

### 9.4 Override artefacts

An `override` artefact MUST carry:

```json
{
  "kind": "override",
  "based_on": "art_01HZ9YX1…",
  "logical_id": "lgl_01HZ9YX1A2B3C4D5E6F7G8H9J0",
  "intent_preserved": true,
  "diff": [
    { "op": "replace", "path": "/content/text",
      "from": "We're sorry for the delay…",
      "to":   "I'm sorry for the delay   I've also waived shipping on your next order." }
  ],
  "rationale": "Compensation offered to retain customer per policy CSAT-3.",
  "tags": ["tone-adjustment", "compensation-offered"],
  "policy_refs": ["CSAT-3"]
}
```

The diff format is JSON Patch ([RFC 6902]) with an additional `from`
field on `replace` operations for context. The rationale is free
text; the tags are workspace-defined categorisations useful for
analysing override patterns across time.

When the override's `based_on` target carries a `logical_id`, the
override SHOULD carry the same `logical_id` and SHOULD set
`intent_preserved` to indicate whether the override changes the
underlying intent (`false`: this is a different decision) or
refines its expression (`true`: same decision, better delivery).
The field is informational; CHAP does not constrain semantics. It
exists because *"the human edited the agent's draft"* and *"the human
replaced the agent's draft with a different decision"* are
operationally different events that produce identical envelope
structures without it.

The same convention applies to `control.supersede`: when superseding
an artefact that carries a `logical_id`, the replacement SHOULD carry
the same `logical_id` and set `intent_preserved` accordingly.

[RFC 6902]: https://www.rfc-editor.org/rfc/rfc6902

### 9.5 Routing hints on artefacts (optional)

An Artefact MAY carry an optional `routing_hints` object that
records production measurements: model confidence, model identifier,
cost incurred, latency. These signals are consumed by the
`routing/1.0` profile to drive review-depth and auto-escalation
decisions; they are recorded in the evidence envelope hash even
when the profile is not in use.

```json
{
  "routing_hints": {
    "confidence": 0.62,
    "model_id": "careful-draft-v2:2026-05",
    "cost_consumed_usd": 3.40,
    "latency_ms": 2810
  }
}
```

Fields:

| Field               | Type    | Constraint                                |
|---------------------|---------|-------------------------------------------|
| `confidence`        | number  | in [0, 1]; model-specific calibration     |
| `model_id`          | string  | recommended whenever `confidence` is set  |
| `cost_consumed_usd` | number  | non-negative                              |
| `latency_ms`        | integer | non-negative                              |

**Calibration caveat.** Two `confidence: 0.83` values from different
models are not comparable without calibration data. CHAP makes no
claim about cross-model comparability and recommends restricting
routing rules that consult `confidence` to a single `model_id` or
model family.

### 9.6 Route-decision artefacts (informative)

The `routing/1.0` profile defines an additional artefact kind,
`route_decision`, recording the outcome of a routing method call
(`task.route`, `review.depth`, or `escalate.auto`). See
[`profiles/routing.md`](./profiles/routing.md) for the schema.

---

## 10. Evidence and audit

### 10.1 Evidence chain

Each workspace maintains a single append-only chain of evidence
entries. Every accepted CHAP message produces exactly one entry.
Entries are linked by SHA-256 hashes:

```
entry_n.prev_hash = SHA-256( JCS(envelope_{n-1} without evidence.sig)
                             || evidence_{n-1}.sig )
```

The chain head is published in the workspace descriptor as
`evidence_head` and the chain length as `evidence_count`.

### 10.2 Verification

Given the workspace's genesis entry and the current head, any verifier
can replay the chain and confirm:

1. Each message's signature verifies against the claimed `from`
   Participant's key as of `ts`.
2. Each `prev_hash` matches the recomputed previous entry hash.
3. Timestamps are monotonically non-decreasing.
4. No `id` is reused.

A `audit.verify` request returns the result of this replay over a
specified range.

### 10.3 Checkpoints

The Coordinator SHOULD emit periodic **checkpoint** entries
(default: every 1000 entries) signed with its long-lived key. A
checkpoint is a notification with method `audit.checkpoint` whose
params include the current head, the entry count, and the
Coordinator's signature over both. Verifiers MAY anchor checkpoints
to external transparency logs.

### 10.4 External anchoring

For deployments requiring stronger tamper-evidence, workspaces MAY
periodically publish their chain head to:

- An internal append-only store with separate access controls.
- A transparency log (e.g. a Trillian-style Merkle log).
- A third-party notarisation service.

Anchoring is referenced from the workspace descriptor's `anchors[]`
array; the format of each anchor reference is anchor-specific.

### 10.5 Retention and redaction

Evidence entries are immutable. To remove a message's content
(e.g. for compliance reasons), the Coordinator SHALL emit a
`audit.redact` entry that replaces the content of a prior entry
with a placeholder while preserving the entry's hash and signature.
Redaction MUST itself be signed by an admin Participant. The
original content is retained only if policy permits; the *fact of
redaction* is permanent.

---

## 11. Modes

### 11.1 The three modes

Every workspace and every task carries a mode:

- **`shadow`**: Output is produced but does not reach external
  effects. Used for offline evaluation, regression testing, and
  pre-deployment review of agent changes.
- **`trial`**: Output reaches a limited audience (specified
  observers or a percentage of traffic) and is still gated for review.
- **`production`**: Output reaches its intended audience with full
  effect.

### 11.2 Promotion

Modes form a strict order: `shadow < trial < production`. Promotion
moves a workspace or task forward in this order; demotion moves it
back. Both transitions are recorded as evidence entries.

A workspace declares a `mode_ceiling` that bounds the maximum mode
its tasks may carry. Setting `mode_ceiling` upward toward
`production` is a privileged operation requiring step-up auth and a
matching policy entry.

### 11.3 Enforcement

The Coordinator MUST:

- Reject any `task.assign` whose mode exceeds the workspace's ceiling
  with error `-32501` (`mode_ceiling_exceeded`).
- Refuse to dispatch shadow-mode artefacts to participants not on
  the workspace's `shadow_observers` list.
- Record every mode change as a first-class evidence entry.
- Reject privileged mode transitions without valid step-up auth with
  error `-32402` (`step_up_required`).

### 11.4 Per-task overrides

A task MAY carry a mode strictly lower than the workspace's current
mode (e.g. running a single task in `shadow` inside an otherwise
`production` workspace, for debugging). It MUST NOT carry a mode
higher than the workspace's mode.

---

## 12. Methods

This section enumerates the method catalogue. The authoritative
machine-readable form is [`schemas/chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json).

Every method has:

- A **namespace** (`workspace`, `participant`, `task`, etc.).
- A **type** (`request` or `notification`).
- A list of **required scopes** for the caller.
- A **privileged** flag indicating whether step-up auth is required.

### 12.1 `workspace.*`

| Method                  | Type         | Privileged | Description                                |
|-------------------------|--------------|------------|--------------------------------------------|
| `workspace.create`      | request      | yes        | Create a new workspace.                    |
| `workspace.describe`    | request      | no         | Return the workspace descriptor.           |
| `workspace.invite`      | request      | yes        | Invite a Participant.                      |
| `workspace.evict`       | request      | yes        | Remove a Participant.                      |
| `workspace.set_mode`    | request      | yes (for promotions toward production) | Change the workspace mode. |
| `workspace.pause`       | request      | yes        | Suspend new task acceptance.               |
| `workspace.resume`      | request      | yes        | Resume operation.                          |
| `workspace.close`       | request      | yes        | Seal the workspace.                        |

### 12.2 `participant.*`

| Method                  | Type         | Privileged | Description                                |
|-------------------------|--------------|------------|--------------------------------------------|
| `participant.describe`  | request      | no         | Return a Participant's descriptor.         |
| `participant.announce`  | notification | no         | A Participant announces presence/availability. |
| `participant.heartbeat` | notification | no         | Periodic liveness signal.                  |
| `participant.rotate_key`| request      | no         | Replace signing key (signed with old key). |
| `participant.revoke_key`| request      | yes        | Mark a key compromised. Admin only.        |

### 12.3 `task.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `task.assign`        | request      | no         | Propose a task to an assignee.                |
| `task.accept`        | response     | no         | Accept an assignment.                         |
| `task.decline`       | response     | no         | Decline an assignment.                        |
| `task.start`         | notification | no         | The assignee has begun work.                  |
| `task.progress`      | notification | no         | Progress update (optional).                   |
| `task.complete`      | request      | no         | Submit a completed task with its artefact.    |
| `task.describe`      | request      | no         | Return a task's current state.                |

### 12.4 `review.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `review.request`     | request      | no         | Ask one or more reviewers to evaluate an artefact. |
| `review.acknowledge` | notification | no         | Reviewer signals they have begun review.      |

### 12.5 `decide.*` / `abstain.*` / `escalate.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `decide.approve`     | request      | no         | Approve a draft as-is.                        |
| `decide.reject`      | request      | no         | Reject a draft with a reason.                 |
| `decide.override`    | request      | no         | Approve a modified version; produces an override artefact. |
| `abstain.declare`    | request      | no         | Decline to decide; flags for escalation.      |
| `escalate.raise`     | request      | no         | Hand a task up the chain with context.        |

### 12.6 `whisper.*` / `capture.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `whisper.ask`        | request      | no         | Quick, deadline-bound interrupt question.     |
| `whisper.answer`     | response     | no         | Answer a whisper.                             |
| `capture.append`     | request      | no         | Append an ad-hoc fragment (note, tag, link) to a task. |

### 12.7 `handoff.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `handoff.propose`    | request      | no         | Propose transferring work to another participant. |
| `handoff.accept`     | response     | no         | Accept a handoff.                             |
| `handoff.decline`    | response     | no         | Decline a handoff.                            |

### 12.7a `routing.*` (profile `routing/1.0`)

| Method            | Type    | Privileged | Description                                       |
|-------------------|---------|------------|---------------------------------------------------|
| `task.route`      | request | no         | Pick an assignee from candidates given `routing_hints`. Produces a `route_decision` artefact. |
| `review.depth`    | request | no         | Decide review depth (`skip` / `spot_check` / `full`). Produces a `route_decision` artefact. |
| `escalate.auto`   | request | no         | Evaluate auto-escalation rules; if a rule fires, escalates to the rule's target. |

These methods are only present when the workspace advertises
`routing/1.0` in `workspace.describe.profiles`. They consume the
optional `routing_hints` fields on Tasks (§8.4) and Artefacts (§9.5)
and write decisions to the evidence chain via `route_decision`
artefacts. The full profile is specified in
[`profiles/routing.md`](./profiles/routing.md).

### 12.8 `notify.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `notify.message`     | notification | no         | Generic free-text message between participants. |
| `notify.alert`       | notification | no         | High-priority alert with a severity field.    |

### 12.9 `deliberate.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `deliberate.open`    | request      | no         | Open a multi-party thread with a decision rule. |
| `deliberate.comment` | notification | no         | Add a comment to an open deliberation.        |
| `deliberate.vote`    | request      | no         | Cast a vote (yea/nay/abstain, optional weight). |
| `deliberate.close`   | request      | no         | Close the deliberation; computes the outcome per rule. |

### 12.10 `control.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `control.pause`      | request      | yes        | Pause a task.                                 |
| `control.resume`     | request      | yes        | Resume a paused task.                         |
| `control.cancel`     | request      | yes        | Cancel a task (terminal).                     |
| `control.supersede`  | request      | yes        | Replace a task with another (terminal).       |
| `control.snapshot`   | request      | yes        | Produce a workspace snapshot artefact.        |
| `control.rollback`   | request      | yes        | Roll back to a prior snapshot.                |

### 12.11 `audit.*`

| Method               | Type         | Privileged | Description                                   |
|----------------------|--------------|------------|-----------------------------------------------|
| `audit.read`         | request      | no         | Read a range of evidence entries.             |
| `audit.verify`       | request      | no         | Verify the chain over a range.                |
| `audit.checkpoint`   | notification | no         | Coordinator-emitted checkpoint.               |
| `audit.redact`       | request      | yes        | Redact a prior entry (preserves hash).        |
| `audit.export`       | request      | yes        | Export the chain in a portable format.        |

---

## 13. Error model

### 13.1 Error object

Error responses carry an `error` object:

```json
{
  "code": -32402,
  "message": "Step-up authentication required.",
  "data": {
    "method": "control.rollback",
    "step_up_window_sec": 300,
    "auth_time_age_sec": 1200
  }
}
```

### 13.2 Error code ranges

Error codes follow JSON-RPC conventions with CHAP-specific extensions:

| Range              | Meaning                                          |
|--------------------|--------------------------------------------------|
| -32700             | Parse error                                      |
| -32600 to -32603   | JSON-RPC standard errors                         |
| -32400 to -32499   | CHAP envelope and identity errors                 |
| -32500 to -32599   | CHAP policy and mode errors                       |
| -32600 to -32699   | CHAP task and lifecycle errors                    |
| -32700 to -32799   | CHAP evidence and audit errors                    |
| -32800 to -32899   | CHAP composition errors (MCP/A2A bridge)          |
| -32900 to -32999   | Implementation-defined                           |

### 13.3 Standard error codes

| Code   | Symbol                          | Meaning                                              |
|--------|---------------------------------|------------------------------------------------------|
| -32700 | `parse_error`                   | Invalid JSON.                                        |
| -32600 | `invalid_request`               | Envelope does not conform to schema.                 |
| -32601 | `method_not_found`              | Unknown method name.                                 |
| -32602 | `invalid_params`                | Params do not conform to method schema.              |
| -32603 | `internal_error`                | Implementation defect.                               |
| -32400 | `signature_invalid`             | Signature failed verification.                       |
| -32401 | `temporal_order_violation`      | Non-monotonic timestamp from origin.                 |
| -32402 | `step_up_required`              | Privileged op without recent auth.                   |
| -32403 | `unknown_participant`           | `from` or `to` is not a workspace member.            |
| -32404 | `key_revoked`                   | Signing key has been revoked.                        |
| -32405 | `version_unsupported`           | `chap` version not recognised.                        |
| -32500 | `policy_denied`                 | Caller's role does not permit the method.            |
| -32501 | `mode_ceiling_exceeded`         | Task mode exceeds workspace ceiling.                 |
| -32502 | `scope_missing`                 | Recipient has not declared the required scope.       |
| -32600 | `task_state_invalid`            | Method illegal in task's current state.              |
| -32601 | `review_rule_unmet`             | Decision cannot terminate the review under the rule. |
| -32602 | `deadline_exceeded`             | Operation arrived after the task deadline.           |
| -32700 | `evidence_break`                | `prev_hash` does not match chain head.               |
| -32701 | `id_reused`                     | `id` already present in chain.                       |
| -32800 | `mcp_tool_failed`               | Cited MCP tool invocation reported failure.          |
| -32801 | `a2a_peer_unreachable`          | Bridge to an A2A peer failed.                        |

Implementations MUST use these codes for the stated conditions and
MAY define implementation-specific codes in the -32900 range.

---

## 14. Transports

CHAP semantics are transport-agnostic. This section defines the
**bindings**: how envelopes are serialised onto specific transports.

### 14.1 Common requirements

For all transports:

- The wire encoding is UTF-8 JSON.
- TLS 1.3+ is REQUIRED in production.
- The transport MUST preserve message boundaries (each envelope is
  a discrete unit).
- The transport SHOULD support back-pressure or rate limiting.
- The transport MUST NOT alter the envelope content (no transport-level
  framing that mutates the JSON).

### 14.2 WebSocket binding (RECOMMENDED)

The WebSocket binding uses `wss://` URLs. Each WebSocket frame
contains exactly one envelope. The subprotocol identifier is
`chap.v1`. Initial connection requires an `Authorization` header
carrying the OIDC ID token or service credential.

> The v0.2 reference implementations use plain HTTP POST; a
> WebSocket reference binding is planned for a future revision.

### 14.3 HTTP+SSE binding (RECOMMENDED)

Two endpoints:

- **`POST /chap`**: single-envelope submission. Returns the
  Coordinator's acknowledgement (a `response` envelope) in the
  HTTP response body.
- **`GET /chap/events`**: Server-Sent Events stream of envelopes
  addressed to the authenticated participant. Each event's `data:`
  field is a single envelope. Events use the `id:` field for the
  envelope's `id`.

The playground at [`reference/playground/`](./reference/playground/)
implements this binding (POST and SSE, with `/rpc` instead of
`/chap` to avoid clashing with the project name).

### 14.4 HTTP polling binding

A degraded mode for clients that cannot maintain a persistent
connection:

- **`POST /chap`**: submission (as above).
- **`GET /chap/inbox?since=<cursor>`**: return all envelopes
  addressed to the authenticated participant since the cursor.

Polling intervals SHOULD NOT exceed 5 seconds in production.

### 14.5 Message broker bindings (NATS, Kafka, RabbitMQ)

For broker-based deployments, the binding rules are:

- One subject/topic/queue per workspace, plus one per
  Participant for direct-addressed messages.
- The message payload is the envelope JSON.
- The broker's message ID MUST match the envelope's `id`.
- Broker-level retention does not replace the CHAP evidence chain;
  evidence is appended by the Coordinator regardless of broker
  durability.

---

## 15. Security considerations

The full threat model is in [SECURITY.md](./SECURITY.md). This
section summarises requirements normative to the specification.

### 15.1 Mandatory protections

Conformant implementations MUST:

1. Verify every signature before accepting any message into the
   evidence chain.
2. Reject messages with non-monotonic timestamps from the same
   origin.
3. Reject messages whose `prev_hash` does not match the current
   chain head.
4. Enforce role/method/scope checks before dispatching.
5. Enforce the mode ceiling and shadow-observer routing rules.
6. Require step-up authentication for privileged methods.
7. Use TLS 1.3+ for all production transports.
8. Use cryptographically random ULIDs for `id` generation.

### 15.2 Recommended protections

Conformant implementations SHOULD:

1. Use ephemeral signing keys for human Participants, bound via OIDC.
2. Use workload identities (SPIFFE, mTLS, OIDC client credentials)
   for agents and services.
3. Anchor chain heads to an external transparency log.
4. Rate-limit per Participant.
5. Apply per-method timeouts and reject stale requests.
6. Use mutual TLS for service-to-service transports.

### 15.3 Confidentiality

CHAP does not encrypt artefact content in the evidence chain.
Sensitive content SHOULD be:

- Referenced by URI (with content held in a separately access-controlled
  store), and the URI's hash committed in the artefact, or
- Replaced inline with the content hash and a short summary.

A confidentiality extension defining per-field encryption is under
discussion for the next draft.

### 15.4 Threat model

This section identifies adversaries CHAP defends against, adversaries
it does not, and the protocol-level countermeasures behind each
defended class. The full operational threat model is in
[SECURITY.md](./SECURITY.md).

**Replay.** An adversary captures a previously-valid envelope and
re-injects it into the chain.  *Countermeasures:* envelope `id` is a
ULID (Crockford-base32; 26 chars) which conformant Coordinators MUST
reject on second observation (error code `-32701 id_reused`); `ts`
MUST be monotonically non-decreasing per `from`; `prev_hash` MUST
match the current chain head, so any replay against a chain that has
since advanced is detected at acceptance.

**Downgrade.** An adversary forces capability negotiation in
`workspace.describe` to advertise fewer profiles than both peers
support, hoping to suppress a defensive profile (e.g.
`security-signed/1.0` or `audit-scitt/1.0`).  *Countermeasures:* the
workspace descriptor is itself an artefact in the evidence chain;
its advertised profile list is signed and cannot be retrospectively
narrowed. Deployments concerned about downgrade SHOULD treat the
profile set as policy: any participant whose `participant.join`
declares a lower profile set than the workspace's mandatory minimum
MUST be refused. The `modes/1.0` profile, combined with workspace
policy, lets an operator pin a floor.

**Capability confusion across profiles.** Two profiles define methods
with similar names but different security properties (for example,
`decide.override` in `review/1.0` versus a hypothetical
`decide.override` in a forked profile). *Countermeasures:* methods
are namespaced (`namespace.verb`) and the profile that owns a
namespace is declared in the workspace descriptor; Coordinators MUST
reject a method call whose namespace's owning profile is not in the
workspace's advertised set.

**Key rotation.** A participant rotates a signing key mid-chain.
*Countermeasures:* `identity-oidc/1.0` and `identity-vc/1.0` define
key rotation as an explicit `participant.update` event signed by the
old key, naming the new key. Verifiers walking the chain MUST treat
the post-rotation entries as signed by the new key only after the
rotation event itself has been verified by the old key. A rotation
event MUST NOT retroactively re-sign earlier entries.

**Evidence-chain forking under partition.** Two Coordinators serving
the same workspace under a network partition each accept envelopes
into their local chain head; on partition heal the chains have
diverged. *Countermeasures:* CHAP's evidence chain is per-workspace
and per-Coordinator; the protocol does not provide a Byzantine fault
tolerant consensus layer. Deployments that require continuity through
partition MUST either (a) run a single logical Coordinator with HA
replication that preserves chain linearity, or (b) operate the
peer-to-peer topology in §3 with each peer maintaining its own chain
and using `audit.read` to cross-verify on heal. Detection of fork is
automatic (the divergent `prev_hash` values do not link) but
resolution is operational. Deployments SHOULD anchor chain heads to
an external transparency log via `audit-scitt/1.0` to make fork
detection independent of the Coordinators themselves.

**Compromised Coordinator.** An adversary controls a Coordinator and
attempts to forge entries, suppress entries, or rewrite history.
*Countermeasures:* signatures are made by the originating
participant, not by the Coordinator, so the Coordinator cannot
forge new participant content; suppression of a delivered envelope
is detectable because the affected participant retains a record of
emission; rewriting history breaks `prev_hash` linkage and, where
deployed, breaks the SCITT receipt's witnessed root. A Coordinator
that is the sole signer of receipts can equivocate; deployments
defending against this MUST use `audit-scitt/1.0` with an externally
operated transparency service whose witnesses are not under the same
administrative control as the Coordinator.

**Identity confusion.** A participant adopts a Participant URI that
resembles another's. *Countermeasures:* Participant URIs in
`human:`, `agent:`, `service:` namespaces MUST be bound to a verified
identity (OIDC subject claim or VC subject DID) before being
admitted to a workspace via `participant.join`. The binding is
recorded in the participant descriptor and signed.

**Out of scope.** CHAP does not defend against: a Participant who
chooses to lie within the schema (a human who clicks Approve having
not read the artefact; an agent that hallucinates a citation); the
content of artefacts (the protocol carries opaque content; semantic
integrity is the deploying application's concern); side-channel
inference on `routing_hints` or other metadata; denial-of-service at
the transport layer (handled by the underlying transport's controls).

---

## 16. Composition with MCP and A2A

CHAP is designed to compose, not replace.

### 16.1 MCP composition

When an agent calls an MCP tool, the call is cited inside the CHAP
artefact it produces. The citation includes:

- The MCP server URI.
- The tool name.
- The call ID.
- The SHA-256 hash of the canonical input and output.

The hashes (not the bodies) are committed to the CHAP evidence chain.
This means: a verifier with access to the MCP server's audit log can
reconstruct the full input and output and confirm they match the
hashes; a verifier without that access still has cryptographic proof
of *which* tool was called and that the recorded inputs and outputs
have not been altered.

See [`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md)
for the full pattern.

### 16.2 A2A composition

When work crosses an organisational boundary, an **A2A bridge
service** participates in both protocols. Inside the local CHAP
workspace, the bridge appears as `service:bridge@example.org`. It
accepts CHAP tasks, forwards them over A2A, returns the result as a
CHAP artefact, and cites the A2A correlation IDs in the artefact's
citations array.

This pattern preserves CHAP's evidence semantics inside the
workspace while delegating cross-system communication to A2A.

See [`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

### 16.3 CHAP as MCP server / A2A agent

Sections 16.1 and 16.2 describe the **outward** composition: a CHAP
workspace cites external MCP or A2A events. The composition also
runs **inward**: a CHAP Coordinator MAY present itself as an MCP
server or an A2A agent, with every CHAP method exposed as a tool
(MCP) or skill (A2A). MCP clients or A2A orchestrators then drive
the workspace directly.

Inward composition is a transport binding, not a wire-format change.
A Coordinator that does and does not expose an inward MCP or A2A
interface produces byte-identical audit chains for the same envelope
sequence. The inward adapter packaged in this repository targets
MCP **2025-11-25**, A2A **0.3.0** (via the TypeScript SDK), and A2A
**1.0** (via the Python SDK). See the implementation notes in
[`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md) §10
and [`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md) §8.

---

## 17. Conformance

### 17.1 Levels

CHAP defines two implementable conformance levels in the current
draft (Minimal, Recommended) and one planned level (Full). An
implementation MAY claim a level only against the method set it has
actually implemented and exercised against the test vectors in
[`conformance/test-vectors.md`](./conformance/test-vectors.md); a
claim against a method declared but not implemented is non-conformant.

#### Minimal

An implementation conforms at the **minimal** level if it:

- Implements the envelope format and schema validation.
- Implements Ed25519 signing and JCS canonicalisation.
- Implements the hash-chained evidence log.
- Implements at least one transport binding.
- Implements `workspace.describe`, `participant.describe`,
  `task.assign`, `task.accept`, `task.complete`, `review.request`,
  `decide.approve`, `decide.reject`, and `audit.read`.
- Enforces the mandatory protections of §15.1.

#### Recommended

A **recommended** implementation additionally:

- Implements `decide.override`, `abstain.declare`, `escalate.raise`,
  `handoff.*`, `whisper.*`, `capture.append`, `audit.verify`, and
  the full `control.*` namespace.
- Implements OIDC-bound human identity (§5.4).
- Implements at least two transports.
- Implements MCP composition (§16.1).
- Publishes a method-role policy document.

#### Full (planned)

A **full** level is reserved for a future revision of this
specification. Reaching Full requires: implementation of all methods
in the catalogue including the profile-defined methods marked
*specified* in the v0.2 method index; A2A composition (§16.2);
external evidence anchoring via `audit-scitt/1.0`; and successful
execution of the published interop test suite against a second,
independent implementation. CHAP 0.2 does not currently have a
second interoperable implementation, so no implementation can
correctly claim the Full level under this revision. Implementations
already meeting the technical requirements above are welcome to
publish a Recommended attestation and a list of additional methods
implemented; promotion to Full will be opened once the interop
substrate is in place.

### 17.2 Self-attestation

Implementations MAY self-attest a conformance level by publishing a
conformance statement listing the implemented methods, transports,
and protections. See [`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md)
for the template. The attestation MUST be honest about which methods
are implemented versus declared; consumers SHOULD treat a method
named in the catalogue but not in the attestation as unavailable in
that implementation.

### 17.3 Interop testing

A formal interop test suite is in draft. The test vectors in
[`conformance/test-vectors.md`](./conformance/test-vectors.md)
provide canonical input/output pairs for signing, canonicalisation,
and evidence chaining that every implementation MUST reproduce
exactly; the harness in
[`conformance/harness/`](./conformance/harness/) provides the
runnable substrate. A full interoperability test suite, covering
end-to-end method exchange between two implementations under both
Coordinator-mediated and peer topologies, is planned alongside the
Full conformance level.

---

## 18. IANA considerations

This specification requests IANA registration of:

- **URI scheme prefixes:** `human:`, `agent:`, `service:`, `group:`,
  `workspace:` under the provisional URI scheme registry.
- **Media type:** `application/chap+json` for envelopes.
- **WebSocket subprotocol:** `chap.v1` in the WebSocket Subprotocol
  Name Registry.
- **OIDC confirmation method:** None new; CHAP reuses the existing
  `cnf.jwk` claim from RFC 7800.

Registrations will be filed when the specification reaches Last Call.

---

## Appendix A: Normative references

- [RFC 2119] Key words for use in RFCs.
- [RFC 8174] Ambiguity of uppercase vs lowercase in RFC 2119 key words.
- [RFC 7517] JSON Web Key (JWK).
- [RFC 7800] Proof-of-Possession Key Semantics for JWTs.
- [RFC 8032] Edwards-Curve Digital Signature Algorithm (EdDSA).
- [RFC 8785] JSON Canonicalization Scheme (JCS).
- [RFC 6902] JavaScript Object Notation (JSON) Patch.
- [RFC 9449] OAuth 2.0 Demonstrating Proof of Possession (DPoP).
- [ULID Specification](https://github.com/ulid/spec).

## Appendix B: Informative references

- Model Context Protocol, https://modelcontextprotocol.io
- Agent-to-Agent (A2A) Protocol, https://a2a.dev
- OpenID Connect Core 1.0, https://openid.net/specs/openid-connect-core-1_0.html
- SPIFFE, https://spiffe.io
