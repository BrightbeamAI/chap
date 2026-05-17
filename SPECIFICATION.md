# Human-Agent Protocol (HAP) — Specification

# Human-Agent Protocol — Specification

**Audience:** Implementers · **Format:** Combined Core + Profiles reference

---

> ### Orientation
>
> This document is the **combined reference** — Core and every
> profile in a single document, cross-referenced and ready for
> implementers who want one place to look up every detail.
>
> For most newcomers, the right entry points are:
>
> - **[README](./README.md)** — overview and reading paths.
> - **[Handbook](./HANDBOOK.md)** — practical operator's manual.
> - **[`core/SPEC.md`](./core/SPEC.md)** — minimal Core specification (weekend-implementable).
> - **[`profiles/PROFILES.md`](./profiles/PROFILES.md)** — profile catalogue.
>
> This document compiles all of the above. It is normative for the
> protocol as a whole; the individual Core and profile documents are
> normative for their respective parts and link back here.

---

## Status of this document

This document specifies the Human-Agent Protocol. The keywords **MUST**,
**MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**,
**SHOULD NOT**, **RECOMMENDED**, **MAY**, and **OPTIONAL** are to be
interpreted as described in [RFC 2119] and [RFC 8174] when,
and only when, they appear in all capitals.

[RFC 2119]: https://www.rfc-editor.org/rfc/rfc2119
[RFC 8174]: https://www.rfc-editor.org/rfc/rfc8174

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
the fact. HAP standardises this layer.

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
6. **Composable with MCP and A2A.** HAP cites tool calls and cross-system
   agent messages inside its own evidence chain.
7. **Boring on purpose.** JSON-RPC-2.0-style envelope, JSON Schema for
   every primitive, Ed25519 + JCS for signing.

### 1.3 Non-goals

HAP is not:

- A user interface specification. It defines a wire format and a method
  catalogue, not the shape of an approval dialog.
- A workflow engine. HAP carries the messages a workflow engine
  produces; it does not itself execute long-running business logic.
- A replacement for MCP or A2A. It composes with both.
- A confidentiality layer for sensitive payloads. Use opaque
  artefact references and external content storage.
- A business-process notation. Mechanism, not policy.

---

## 2. Terminology

This section defines terms used normatively throughout the document. See
[GLOSSARY.md](./GLOSSARY.md) for an extended glossary with adjacent terms.

- **Workspace.** A named, addressable collaboration context with a
  membership list, a policy, a mode, and an append-only evidence log.
- **Participant.** Any entity that can send or receive HAP messages
  inside a workspace. Participants are typed as `human`, `agent`,
  `service`, `group`, or `workspace`.
- **Coordinator.** The component that mediates a workspace: routes
  messages, enforces policy and mode, and appends entries to the
  evidence chain. The Coordinator is a Participant of type `service`.
- **Task.** A unit of work proposed, accepted, performed, and resolved
  inside a workspace. Tasks have a lifecycle and produce artefacts.
- **Artefact.** A typed payload produced by a Participant in the
  course of a task — a draft, a decision, an override, a citation set,
  a structured record.
- **Override.** An artefact that records a human's modification of an
  agent's output, including the diff, rationale, and applicable tags.
- **Evidence entry.** A signed, hash-linked record of a single HAP
  message inside a workspace's evidence log.
- **Mode.** The operational regime of a workspace or a specific task:
  `shadow`, `trial`, or `production`.

---

## 3. Protocol stack and positioning

HAP sits alongside MCP and A2A as the third layer of the agent-protocol
stack. The three protocols address disjoint concerns:

| Protocol | Concern                          | Primary endpoints      |
|----------|----------------------------------|------------------------|
| **MCP**  | An agent calling a tool          | Agent ↔ Tool server    |
| **A2A**  | Agents talking across systems    | Agent ↔ Agent          |
| **HAP**  | The shared collaboration room    | Human ↔ Agent ↔ Human  |

A typical deployment looks like:

```
┌────────────────────────────────────────────────────────────────────┐
│                          HAP Workspace                              │
│  (humans, agents, services as peers; one evidence chain)            │
│                                                                     │
│   human ──┐                       ┌── agent ──[ MCP ]── tool        │
│           ├─ HAP ─ Coordinator ─┤                                   │
│   human ──┘                       └── agent ──[ A2A ]── peer        │
└────────────────────────────────────────────────────────────────────┘
```

When an agent calls a tool over MCP, the call and its result are cited
inside the HAP evidence chain so that a single audit covers the full
human-agent-tool path. When an agent delegates work to a peer in another
organisation over A2A, a HAP bridge participant represents the remote
work in the local workspace.

---

## 4. Wire format

### 4.1 Envelope

Every HAP message is a JSON object conforming to
[`schemas/hap-envelope.schema.json`](./schemas/hap-envelope.schema.json).

```json
{
  "hap": "0.1",
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
| `hap`       | string (SemVer) | yes      | Wire version. Implementations MUST refuse unrecognised major versions.      |
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

HAP uses a JSON-RPC-2.0-inspired but not identical three-type model:

- **`request`** — solicits a response. Carries `method` and `params`.
  The Coordinator MAY answer requests directly (e.g. for routing or
  policy queries) but typically forwards to the addressed Participant,
  which replies with a `response` whose `id` echoes the request's `id`.
- **`response`** — answers a previous `request`. Carries `result` on
  success or `error` on failure. The `id` MUST match the request's `id`.
- **`notification`** — fire-and-forget. Carries `method` and `params`.
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

Every HAP message MUST be signed. The signature algorithm is
**Ed25519** ([RFC 8032]). The signed input is the
**JCS canonicalisation** ([RFC 8785]) of the envelope **with the
`evidence.sig` field removed** but `evidence.prev_hash` retained.

[RFC 8032]: https://www.rfc-editor.org/rfc/rfc8032
[RFC 8785]: https://www.rfc-editor.org/rfc/rfc8785

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
`ts`* — keys may have rotated since.

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

- A leaked long-term password cannot be replayed against HAP.
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

In every case, the signing key for HAP messages is bound to the
workload identity. Long-lived agent identifiers (like
`agent:triage-bot`) MAY map to a sequence of short-lived keys; the
mapping is published in the participant descriptor.

### 5.6 Step-up authentication

Methods marked `privileged: true` in the method catalogue
(see §12 and [`schemas/hap-methods.schema.json`](./schemas/hap-methods.schema.json))
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
[`schemas/hap-workspace.schema.json`](./schemas/hap-workspace.schema.json):

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

- **`coordinator`** — exactly one Participant per workspace, of
  type `service`. Holds routing and mode-enforcement authority.
- **`admin`** — one or more Participants, of type `human` or
  `service`. May invite, evict, set mode, and rotate Coordinator
  responsibilities.

All other role names are deployment-defined. The workspace's
`policy_uri` describes which roles may invoke which methods.

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
conforming to [`schemas/hap-participant.schema.json`](./schemas/hap-participant.schema.json):

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
and at what rate. This is **descriptive, not prescriptive** — the
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

A Task conforms to [`schemas/hap-task.schema.json`](./schemas/hap-task.schema.json):

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

- `any_one_approves` — first `decide.approve` wins.
- `all_approve` — every named reviewer must approve.
- `quorum:<n>` — `n` approvals required.
- `weighted_vote:<threshold>` — weighted approvals summing to
  threshold (weights in workspace policy).
- `weighted_vote_with_veto:<threshold>` — as above, but any
  `decide.reject` from a reviewer with `veto: true` ends the review
  immediately as rejected.

---

## 9. Artefacts

### 9.1 Purpose

An Artefact is a typed payload produced inside a workspace — a draft
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
  "content": {
    "text": "Hello — thank you for reaching out about order #...",
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
  "diff": [
    { "op": "replace", "path": "/content/text",
      "from": "We're sorry for the delay…",
      "to":   "I'm sorry for the delay — I've also waived shipping on your next order." }
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

[RFC 6902]: https://www.rfc-editor.org/rfc/rfc6902

---

## 10. Evidence and audit

### 10.1 Evidence chain

Each workspace maintains a single append-only chain of evidence
entries. Every accepted HAP message produces exactly one entry.
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

- **`shadow`** — Output is produced but does not reach external
  effects. Used for offline evaluation, regression testing, and
  pre-deployment review of agent changes.
- **`trial`** — Output reaches a limited audience (specified
  observers or a percentage of traffic) and is still gated for review.
- **`production`** — Output reaches its intended audience with full
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
machine-readable form is [`schemas/hap-methods.schema.json`](./schemas/hap-methods.schema.json).

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

Error codes follow JSON-RPC conventions with HAP-specific extensions:

| Range              | Meaning                                          |
|--------------------|--------------------------------------------------|
| -32700             | Parse error                                      |
| -32600 to -32603   | JSON-RPC standard errors                         |
| -32400 to -32499   | HAP envelope and identity errors                 |
| -32500 to -32599   | HAP policy and mode errors                       |
| -32600 to -32699   | HAP task and lifecycle errors                    |
| -32700 to -32799   | HAP evidence and audit errors                    |
| -32800 to -32899   | HAP composition errors (MCP/A2A bridge)          |
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
| -32405 | `version_unsupported`           | `hap` version not recognised.                        |
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

HAP semantics are transport-agnostic. This section defines the
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
`hap.v1`. Initial connection requires an `Authorization` header
carrying the OIDC ID token or service credential.

See [`reference/transport-ws.ts`](./reference/transport-ws.ts) for
a reference implementation.

### 14.3 HTTP+SSE binding (RECOMMENDED)

Two endpoints:

- **`POST /hap`** — single-envelope submission. Returns the
  Coordinator's acknowledgement (a `response` envelope) in the
  HTTP response body.
- **`GET /hap/events`** — Server-Sent Events stream of envelopes
  addressed to the authenticated participant. Each event's `data:`
  field is a single envelope. Events use the `id:` field for the
  envelope's `id`.

See [`reference/transport-http-sse.ts`](./reference/transport-http-sse.ts).

### 14.4 HTTP polling binding

A degraded mode for clients that cannot maintain a persistent
connection:

- **`POST /hap`** — submission (as above).
- **`GET /hap/inbox?since=<cursor>`** — return all envelopes
  addressed to the authenticated participant since the cursor.

Polling intervals SHOULD NOT exceed 5 seconds in production.

### 14.5 Message broker bindings (NATS, Kafka, RabbitMQ)

For broker-based deployments, the binding rules are:

- One subject/topic/queue per workspace, plus one per
  Participant for direct-addressed messages.
- The message payload is the envelope JSON.
- The broker's message ID MUST match the envelope's `id`.
- Broker-level retention does not replace the HAP evidence chain;
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

HAP does not encrypt artefact content in the evidence chain.
Sensitive content SHOULD be:

- Referenced by URI (with content held in a separately access-controlled
  store), and the URI's hash committed in the artefact, or
- Replaced inline with the content hash and a short summary.

A confidentiality extension defining per-field encryption is under
discussion for the next draft.

---

## 16. Composition with MCP and A2A

HAP is designed to compose, not replace.

### 16.1 MCP composition

When an agent calls an MCP tool, the call is cited inside the HAP
artefact it produces. The citation includes:

- The MCP server URI.
- The tool name.
- The call ID.
- The SHA-256 hash of the canonical input and output.

The hashes (not the bodies) are committed to the HAP evidence chain.
This means: a verifier with access to the MCP server's audit log can
reconstruct the full input and output and confirm they match the
hashes; a verifier without that access still has cryptographic proof
of *which* tool was called and that the recorded inputs and outputs
have not been altered.

See [`integrations/HAP-with-MCP.md`](./integrations/HAP-with-MCP.md)
for the full pattern.

### 16.2 A2A composition

When work crosses an organisational boundary, an **A2A bridge
service** participates in both protocols. Inside the local HAP
workspace, the bridge appears as `service:bridge@example.org`. It
accepts HAP tasks, forwards them over A2A, returns the result as a
HAP artefact, and cites the A2A correlation IDs in the artefact's
citations array.

This pattern preserves HAP's evidence semantics inside the
workspace while delegating cross-system communication to A2A.

See [`integrations/HAP-with-A2A.md`](./integrations/HAP-with-A2A.md).

---

## 17. Conformance

### 17.1 Levels

HAP defines three conformance levels:

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

#### Full

A **full** implementation additionally:

- Implements all methods in the catalogue.
- Implements A2A composition (§16.2).
- Implements external evidence anchoring.
- Passes the published interop test suite (when available).

### 17.2 Self-attestation

Implementations MAY self-attest a conformance level by publishing a
conformance statement listing the implemented methods, transports,
and protections. See [`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md)
for the template.

### 17.3 Interop testing

A formal interop test suite is planned for the next draft. In the
interim, the test vectors in
[`conformance/test-vectors.md`](./conformance/test-vectors.md)
provide canonical input/output pairs for signing, canonicalisation,
and evidence chaining that every implementation MUST reproduce.

---

## 18. IANA considerations

This specification requests IANA registration of:

- **URI scheme prefixes:** `human:`, `agent:`, `service:`, `group:`,
  `workspace:` under the provisional URI scheme registry.
- **Media type:** `application/hap+json` for envelopes.
- **WebSocket subprotocol:** `hap.v1` in the WebSocket Subprotocol
  Name Registry.
- **OIDC confirmation method:** None new; HAP reuses the existing
  `cnf.jwk` claim from RFC 7800.

Registrations will be filed when the specification reaches Last Call.

---

## Appendix A — Normative references

- [RFC 2119] Key words for use in RFCs.
- [RFC 8174] Ambiguity of uppercase vs lowercase in RFC 2119 key words.
- [RFC 7517] JSON Web Key (JWK).
- [RFC 7800] Proof-of-Possession Key Semantics for JWTs.
- [RFC 8032] Edwards-Curve Digital Signature Algorithm (EdDSA).
- [RFC 8785] JSON Canonicalization Scheme (JCS).
- [RFC 6902] JavaScript Object Notation (JSON) Patch.
- [RFC 9449] OAuth 2.0 Demonstrating Proof of Possession (DPoP).
- [ULID Specification](https://github.com/ulid/spec).

## Appendix B — Informative references

- Model Context Protocol — https://modelcontextprotocol.io
- Agent-to-Agent (A2A) Protocol — https://a2a.dev
- OpenID Connect Core 1.0 — https://openid.net/specs/openid-connect-core-1_0.html
- SPIFFE — https://spiffe.io
