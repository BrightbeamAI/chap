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
> - **[START_HERE](./START_HERE.md)**: one recorded decision, in about two minutes.
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
specification's two reference implementations, the TypeScript and
Python coordinators in this repository, are authored by the same team,
where a standards-track promotion would expect independently authored
implementations; a conformance harness covering Core and `review/1.0`
is published as a draft (see [`conformance/`](./conformance/)). Before
1.0 a minor release may break the wire format, and the changelog lists
each break with a migration; from 1.0 the specification follows
Semantic Versioning ([ROADMAP.md](./ROADMAP.md), "Version numbers").
The profile surface should be expected to evolve faster than Core.
Production pilots are welcome to feed back findings; deployments that
need stability guarantees should wait for 1.0.

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
  model-reported value, carried as a decimal string (§9.5). CHAP makes
  no claim about cross-model comparability or about what any
  particular value implies for routing.
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
  evidence chain. The Coordinator is not a member of the workspaces it
  mediates.
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

> **Note.** [`core/SPEC.md`](./core/SPEC.md) §2 describes the messages
> both reference coordinators send today: JSON-RPC 2.0, with the CHAP
> fields inside `params` and, under `security-signed/1.0`, a top-level
> `sig`. Milestone 0.4 settles the message format for the whole
> specification. The envelope in this section is not what either
> coordinator sends. The requirement in its `ts` row that `ts` be
> monotonic per `from` is withdrawn; §4.3 says how `ts` is treated.

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
  "method": "task.create",
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

The `ts` field MUST be UTC with millisecond precision. It is declared by
the sender and carries the sender's clock, so it is not the protocol's
ordering. The Coordinator records `arrived` on each audit entry from its
own clock and assigns `seq` in acceptance order, and it is that pair, with
`prev_hash`, that orders the chain.

A sender whose `ts` goes backwards is worth an operator's attention, and
[SECURITY.md](./SECURITY.md#sender-declared-timestamps) describes the check
and what it costs. Neither coordinator refuses a call for the order of its
`ts`, and no error code is allocated for that. Where signatures are
required, `ts` selects the sender's key (§5.2).

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

> **Note.** [`core/SPEC.md`](./core/SPEC.md) §2 describes the messages
> both reference coordinators send today: JSON-RPC 2.0, with the CHAP
> fields inside `params` and, under `security-signed/1.0`, a top-level
> `sig` (see [`profiles/security-signed.md`](./profiles/security-signed.md)).
> Milestone 0.4 settles the message format, and with it what a signature
> covers, for the whole specification. The procedure below signs the
> envelope of §4.1.

Signing is the `security-signed/1.0` profile (§6.5). A Coordinator that
requires signatures MUST verify one on every call except
`workspace.create` and `participant.join`, which run before the sender
has a registered key. The signature algorithm is **Ed25519**
([RFC 8032]). The signed input is the **JCS canonicalisation**
([RFC 8785]) of the envelope **with the `evidence.sig` field removed**
but `evidence.prev_hash` retained.

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

`participant.describe` and `jwks_uri` are specified and not yet built.
In both references a member's keys appear as `jwks` and `key_history`
in its entry in the `workspace.describe` descriptor (§6.2).

[RFC 7517]: https://www.rfc-editor.org/rfc/rfc7517

Keys carry a `kid` (key ID). The `evidence.sig` field MAY be prefixed
with a key ID hint: `ed25519:<kid>:<base64-signature>`.

### 5.4 Human identity binding

Human participants SHOULD use **ephemeral signing keys** bound to an
OIDC ID token. The binding follows DPoP ([RFC 9449]) in spirit:

1. The client generates an Ed25519 keypair at session start.
2. The client requests an OIDC ID token carrying a `cnf.jwk` claim
   whose value is the public key.
3. On a `participant.join` carrying the ID token as `oidc_token`, the
   Coordinator verifies it and pins the `cnf.jwk` as a signing key for
   this human Participant. The key has no expiry: it stays valid until
   rotated or revoked (§5.7). A token binds only to the participant it
   belongs to: one whose `chap_participant_uri` names another
   participant is refused with `-32404`, and a later join under an
   existing member's name is accepted with a token only for the member's
   recorded `sub`, or, for a member with no recorded subject, a token
   whose `chap_participant_uri` names the member (`-32404` otherwise).
   An accepted later join adds the token's key without retiring the old
   one. A presentation is accepted on a later join only from the
   member's recorded holder (`-32411`, profiles/identity-vc.md §4).
4. The Coordinator MAY require periodic re-binding (token refresh +
   key rotation) for long-lived sessions.

[RFC 9449]: https://www.rfc-editor.org/rfc/rfc9449

This pattern guarantees that:

- A password is presented only to the IdP. Anyone who can authenticate
  there as the member can bind a new key, so the IdP's controls and
  `min_acr` guard the binding.
- The audit chain ties every signed action to a specific
  authentication event, addressable by `auth_time` and `acr`.

A leaked ephemeral key stays usable until rotated or revoked; a
deployment SHOULD revoke it when the session ends.

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
(see §12 and [`schemas/profiles/chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json))
require step-up authentication. Of the methods the references
implement, these are every `control.*` method, `workspace.set_profiles`,
`participant.rotate_key` and `participant.revoke_key`. A Coordinator
enforcing step-up, an option separate from advertising
`identity-oidc/1.0`, MUST refuse them with `-32402` from a human or
OIDC-bound member whose latest `auth_time` is missing or older than the
window (default: 5 minutes, published in the workspace descriptor as
`step_up_window_sec`), or whose `acr` differs from the workspace's
`min_acr` where one is set.

### 5.7 Key rotation

Participants rotate keys with `participant.rotate_key`. The request
includes the new public key (as a JWK) and is signed with the *old*
key. After acceptance:

- Messages from the old key are accepted for verification of historical
  evidence indefinitely.
- Where signatures are required, a new message signed with the old key
  is refused with `-32071` from the moment of rotation; there is no
  grace window.

Keys are revoked with `participant.revoke_key`. A Participant MAY revoke
its own key; revoking another member's key requires the `admin` role
and is otherwise refused with `-32011`. Revocation is recorded in the
evidence chain.

---

## 6. Workspaces

### 6.1 Lifecycle

A workspace is created with `workspace.create`. The creator becomes
the initial admin. This is specified and not yet built: neither
reference makes the creator a member, so the creator joins with
`participant.join` like anyone else.

A workspace starts `active`. `control.pause` with `scope: "workspace"`
moves it to `paused`, and `control.resume` with the same scope moves it
back to `active`. A paused workspace refuses every method with `-32063`
except `workspace.create`, `workspace.describe`, `audit.read`,
`participant.join`, `participant.leave` and `control.resume`, so task
methods, `workspace.set_profiles`, `participant.revoke_key` and
`audit.verify_chain` wait until it resumes.

`closed` is reserved for `workspace.close` (§12.1), which no reference
implements yet. A closed workspace accepts no new operations of any
kind, and its evidence chain is sealed. `archived`, a read-only state
after `closed`, is likewise specified and not yet built.

### 6.2 Descriptor

`workspace.describe` returns a descriptor conforming to
[`schemas/chap-workspace.schema.json`](./schemas/core/chap-workspace.schema.json):

```json
{
  "id": "wsp_support_triage",
  "created": "2026-05-01T09:00:00.000Z",
  "state": "active",
  "mode": "production",
  "mode_ceiling": "production",
  "max_envelope_bytes": 1048576,
  "step_up_window_sec": 300,
  "profiles": ["core/1.0", "review/1.0", "audit-scitt/1.0"],
  "members": [
    { "uri": "human:alice@example.org", "type": "human",
      "role": "reviewer", "joined": "2026-05-01T09:01:00.000Z" },
    { "uri": "agent:triage-bot#v3.2", "type": "agent",
      "role": "drafter", "joined": "2026-05-01T09:02:00.000Z" }
  ],
  "audit_count": 14823,
  "task_count": 3120,
  "override_count": 211,
  "evidence_head": "sha256:8b1c…d9e0"
}
```

`evidence_head` appears only where the workspace keeps a chain, and
`routing_policy_uri` only where set. Member entries add `display_name`,
`capabilities`, `scopes`, `jwks`, `key_history`, `paused`, `oidc_sub`
and `vc_holder` when set.

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

The `coordinator` role is specified and not yet built: neither
reference assigns it, and the Coordinator is not a member (§2).

#### 6.3.1 Actor membership (precondition)

The `from` field of every method names the **actor**: the Participant
on whose behalf the envelope is sent. The actor MUST be a current
member of the named workspace at the time the envelope is processed,
and a Coordinator MUST reject an envelope whose `from` is not a joined
member. The exemptions are:

- `workspace.create` and `participant.join`, which run before the
  sender is a member;
- the reads `workspace.describe` and `audit.read`, unless the
  Coordinator is configured to require membership for reads;
- `audit.verify_chain`, `audit.verify_receipt` and
  `audit.submit_to_scitt`.

Every other method is bound by it, the `routing/1.0` methods and
`participant.leave` included. The refusal is `-32011` (`not_authorised`,
[`profiles/review.md`](./profiles/review.md) §5), and
`participant.rotate_key` refuses a non-member with `-32071`. Enforcing
the precondition makes the audit log's attribution sound: a recorded
decision, completion, or review request can never name a Participant
who never joined.

Membership is the floor, not the ceiling. Individual profiles MAY
impose a stricter eligibility rule on top of it. In particular, the
`review/1.0` profile requires that the actor of a review decision
(`decide.approve`, `decide.reject`, `decide.override`, `abstain.declare`)
be one of the reviewers the review was addressed to in `review.request`'s
`to` set; see [`profiles/review.md`](./profiles/review.md). A `to` entry
that is a broadcast scope (`workspace:<id>` or `group:<name>`) is satisfied
by any workspace member: the Coordinator does not model group membership,
so a `group:` target means "any member", not "any member of that named
group". Deployments that need a decision restricted to a named
group MUST enforce that restriction externally (for example via an
`identity-*` profile or an application-layer check). A future profile MAY
introduce a first-class group-membership model. Membership
verification is distinct from, and composes with, identity verification:
the `identity-oidc/1.0` and `identity-vc/1.0` profiles bind a verified
real-world identity to a Participant, but the membership precondition
here applies whether or not those profiles are in force.

Legitimately admitting a new actor (an escalation target, or an
emergency "break-glass" approver) is done by joining them first, which
records the entry into the workspace as its own audit event. Outside
the exemptions above, there is no path by which a non-member acts; the
exceptional nature of an admission is captured in how, and under what
role, the Participant joined.

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

### 6.5 What advertising a profile does

A workspace's `profiles` list is normative, and this table says what each
entry means. §15.4's dispatch rule follows from it.

| Profile | What advertising it does |
|---|---|
| `core/1.0` | Nothing. Core is always present, and a Core method is never refused for want of an entry. |
| `review/1.0` | Admits `review.request`, `decide.approve`, `decide.reject`, `decide.override`, `abstain.declare` and `escalate.raise`. |
| `modes/1.0` | Makes a `trial` task require review whatever its own `review_required` says. Task modes and the mode ceiling of §11 apply whether or not it is advertised. |
| `control/1.0` | Admits `control.*`: pause, resume, cancel, supersede, snapshot, rollback and the mode ceiling. |
| `whisper/1.0` | Admits `whisper.ask` and `whisper.answer`. |
| `deliberation/1.0` | Admits `deliberate.open`, `deliberate.vote`, `deliberate.comment` and `deliberate.close`. |
| `handoff/1.0` | Admits `handoff.propose`, `handoff.accept` and `handoff.decline`. |
| `routing/1.0` | Admits `task.route`, `review.depth` and `escalate.auto`. |
| `audit-scitt/1.0` | Turns the hash-linked chain on, and admits `audit.submit_to_scitt`. The reads are never gated and are listed below. |
| `security-signed/1.0` | States that every accepted call except `workspace.create` and `participant.join` carries a verified signature. A Coordinator configured to require signatures MUST add this entry at `workspace.create`, and MUST refuse a `workspace.create` carrying it where signatures are not required; `workspace.set_profiles` repeats neither check. |
| `identity-oidc/1.0` | States that a token verifier is configured, under the same rules as `security-signed/1.0`. Step-up freshness is a separate option. |
| `identity-vc/1.0` | Informational: no reference checks it against its configuration. |

Two sets of methods are never refused for want of an entry, whichever profile
owns them:

- **The reads.** `workspace.describe`, `audit.read`, `audit.verify_chain` and
  `audit.verify_receipt`. A workspace MUST be able to say what it is and to
  check its own chain. `audit.verify_chain` belongs to `audit-scitt/1.0` while
  chaining also turns on through an implementation option, so gating it would
  let a workspace write a hash-linked chain it is refused permission to verify.
- **The key lifecycle.** `participant.rotate_key` and `participant.revoke_key`
  belong to Core, so an operator can respond to a compromised key in every
  deployment. A conformant Coordinator answers them whatever the workspace
  advertises.

Advertising a security profile does not turn its enforcement on. The rule
runs one way only: what is enforced MUST be advertised. A deployment advertising
`security-signed/1.0` today without signing would otherwise break at its second
call rather than at configuration time, and the descriptor understating
enforcement is the failure that matters, because a relying party reads the
descriptor to decide what the chain is worth.

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
  "scopes": ["task.update", "task.complete", "review.request"],
  "supported_methods": [
    "task.update", "task.complete", "review.request",
    "whisper.ask", "abstain.declare"
  ],
  "mcp_servers": [
    { "uri": "mcp+https://tools.example.org/orders", "name": "order-lookup" }
  ]
}
```

`participant.describe` is specified and not yet built. Today a member's
entry in the `workspace.describe` descriptor (§6.2) carries part of
this shape: `participant.join` keeps `display_name`, `capabilities`,
`scopes` and keys, and drops `version`, `supported_methods` and
`mcp_servers`.

### 7.2 Capability profile

The `capabilities` block describes what the Participant is good at
and at what rate. This is **descriptive**: the Coordinator stores the
block at `participant.join`, returns it in `workspace.describe` and
reads none of it. The Participant's own authority is unchanged by
claiming any particular capability.

Capability fields:

| Field             | Type            | Description                                              |
|-------------------|-----------------|----------------------------------------------------------|
| `kinds`           | string[]        | Task kinds the Participant can perform.                  |
| `modes`           | enum[]          | Modes the Participant may operate in.                    |
| `max_concurrent`  | integer         | Maximum simultaneous tasks the Participant claims. No reference throttles on it. |
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
created ──▶ in_progress ──▶ review_requested ──▶ completed
   │             │                  │
   │             └──────────────────┴──▶ declined
   │                                └──▶ abstained
   └──────────────────────────────────▶ escalated
   └──────────────────────────────────▶ paused ──▶ in_progress
   └──────────────────────────────────▶ cancelled
   (any state)                       ──▶ superseded
```

A Task has ten states: `created`, `in_progress`, `review_requested`,
`completed`, `declined`, `abstained`, `escalated`, `paused`, `cancelled`
and `superseded`. Terminal states are `completed`, `cancelled` and
`superseded`. `control.supersede` moves a task out of any of them, and
`review.request` reopens a `completed` task for review (see below); no
other method moves a task out of one.

The lifecycle is driven by `task.create`, `task.update`, `task.complete`,
`review.request`, `decide.*`, `abstain.declare`, `escalate.raise` and
`control.*`. The table below is exhaustive and normative: a transition it
does not list MUST be refused. The `From` column names states rather than
categories, because several methods carry preconditions of their own.

| From | Method | To |
|------|--------|-----|
| created, in_progress | `task.complete` (review not required) | completed |
| created, in_progress | `task.complete` (review required) | review_requested, output held as the artefact under review |
| created | `task.update` | in_progress, declined |
| in_progress | `task.update` | in_progress, completed, declined, review_requested |
| in_progress | `task.update` to completed (review required) | refused, -32602 |
| review_requested | `task.update` | in_progress |
| paused | `task.update` | cancelled |
| created, in_progress, completed, declined, abstained, escalated | `review.request` | review_requested |
| review_requested | `review.request` (same artefact) | review_requested, reviewer set widened |
| review_requested | `review.request` (different artefact) | refused, -32014 |
| cancelled, superseded, paused | `review.request` | refused, -32010 |
| review_requested | `decide.approve` | completed once the review rule is satisfied, otherwise review_requested |
| review_requested | `decide.reject` | declined, or in_progress with `request_revision` |
| review_requested | `decide.override` | completed, with an override artefact |
| review_requested | `abstain.declare` | abstained |
| created, in_progress, review_requested, declined, abstained, escalated, paused | `escalate.raise` | escalated |
| created, in_progress, review_requested, abstained, escalated, paused | `control.pause` | paused |
| paused | `control.resume` | the state held at the pause: created, in_progress, review_requested, abstained or escalated; in_progress where none was captured |
| created, in_progress, review_requested, abstained, escalated, paused | `control.cancel` | cancelled |
| any state | `control.supersede` | superseded |

Preconditions that are narrower or wider than "non-terminal":

- **`task.complete` and `review.request` refuse a stopped task**, so that
  neither revives a terminated task nor steps around a pause.
  `review.request` accepts `completed`: completing a task and then requesting
  review of its output is how a draft is commonly submitted, and
  `review_required` is the alternative to that pattern.
- **`control.pause` and `control.cancel` treat `declined` as settled** as well
  as the three terminal states, and answer `-32061`.
- **`abstain.declare` requires an open review**, not merely a non-terminal
  task, and answers `-32010` otherwise.
- **`escalate.raise` accepts any non-terminal state**, including `declined`,
  `abstained` and `escalated`. A second escalation on an already escalated
  task replaces its `superseded_by` link with the newer successor.
- **Pausing is `control.pause` alone.** `task.update` reaches no paused
  state in either direction, so pause and resume both require
  `control/1.0`. A workspace that could pause through Core and resume
  only through `control/1.0` would let any member strand a task that
  the workspace's own profile set could not lift.
- **`control.pause` on a paused task succeeds and changes nothing.**
- **`control.resume` restores the state captured at the corresponding
  `control.pause`.** A review paused mid-flight is therefore actionable again
  on resume. `in_progress` is the result only where no prior state was
  captured, for example a task resumed from a snapshot written before this was
  recorded. A second `control.pause` on an already paused task captures
  nothing, so the state held at the first pause is the one restored.
- **`task.update` MUST NOT complete a task that requires review.**
  `in_progress → completed` is otherwise legal, so without this the review
  could be skipped: the task would finish carrying no artefact and no
  `decide.*` would appear on the chain. The refusal is `-32602`, and the
  route through is `task.complete` followed by a reviewer decision.
- **`review.request` compares only a rule the caller supplied.** Omitting
  `rule` on a second request for the same artefact leaves the open review's
  rule in place and widens the reviewer set; it is not a change to the rule.

A task whose review is required does not complete on `task.complete`. The
call opens a review instead, holding the submitted output as the artefact
under review, and only a reviewer decision then reaches `completed`. Review
is required when the task carries `review_required`, or when it runs in
`trial` mode on a workspace that has loaded `modes/1.0`. The successor that
`escalate.raise` or `control.supersede` makes requires review when the task
it replaces did, so neither removes a required review.

`task.complete` reads no reviewer list from `to`, so the Coordinator
selects the reviewer set: the members of `type: "human"` other than the
completer and the assignee. Where none qualifies the completion MUST be
refused with `-32011`. An explicit `review.request` keeps whatever `to`
it was given. See [`profiles/review.md`](./profiles/review.md) §3.1.

The finer-grained `task.assign` / `task.accept` / `task.start` /
`task.progress` lifecycle reserved in [§12.3](#123-task) is not part of this
state machine, and the states it implies, `assigned` and `accepted`, are not
task states.

### 8.2 Task descriptor

No method returns a task yet (`task.describe` is reserved, §12.3). Both
references hold this shape plus a `history` list, adding
`routing_hints`, `output`, `confidence`, `supersedes`, `superseded_by`
and `paused_from` when set:

```json
{
  "id": "tsk_01HZ9YWQ7K3X8M2V4N6P8R0T3B",
  "kind": "draft_response",
  "state": "review_requested",
  "mode": "production",
  "assignee": "agent:triage-bot#v3.2",
  "delegator": "human:alice@example.org",
  "input": { "ticket_id": "INC-48219" },
  "deadline": "2026-05-17T09:30:00Z",
  "review_required": true,
  "review": {
    "requested_at": "2026-05-17T09:14:56.012Z",
    "requested_to": ["human:bob@example.org"],
    "rule": "any_one_approves",
    "decisions": []
  },
  "created_at": "2026-05-17T09:14:22.184Z",
  "updated_at": "2026-05-17T09:14:56.012Z"
}
```

`task.create` takes the top-level `review_required` and reads no nested
`review` or `constraints`. The schema in
[`schemas/chap-task.schema.json`](./schemas/core/chap-task.schema.json)
does not match this shape: it requires `workspace` and `created`, which
neither reference holds on a task.

### 8.3 Review rules

The `review.rule` field, set by the `rule` parameter of
`review.request`, defines the predicate for moving from
`review_requested` to `completed`. `review/1.0` supports the rules
below and refuses others with `-32602`:

- `any_one_approves` (the default): the first `decide.approve`
  completes the task.
- `all_approve`: every named reviewer must approve. A broadcast-only
  review completes on the first approval.
- `quorum:<n>`: approvals from `n` distinct reviewers, `n` at least 1.

Under every rule one `decide.reject` ends the review: `declined`, or
`in_progress` with `request_revision`. One `decide.override` completes
it. Weighted rules belong to `deliberate.open` (`deliberation/1.0`),
which takes weights and vetoes as parameters.

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
    "max_cost_usd": "49.99",
    "risk_tier": "financial-tier-2"
  }
}
```

Fields:

| Field          | Type    | Constraint                                        |
|----------------|---------|---------------------------------------------------|
| `criticality`  | string  | one of `low`, `medium`, `high`, `critical`        |
| `deadline`     | string  | RFC 3339 timestamp; when the work is needed       |
| `max_cost_usd` | integer or decimal string | non-negative; a fractional amount MUST be a decimal string (§5.2), such as `"49.99"` |
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
extraction, a citation set. An artefact sent in a request, such as a
`review.request` `artefact` or a `task.complete` `output`, is recorded
in the chain with that request. The override records, snapshots and
`route_decision` records the Coordinator builds are held in workspace
state under the `art_` identifier it returns; the chain holds only the
request that produced each.

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
      "kind": "mcp_tool_call",
      "server": "mcp+https://tools.example.org/orders",
      "tool": "lookup_order",
      "input_hash": "sha256:b2c3…",
      "output_hash": "sha256:d4e5…"
    }
  ],
  "confidence": "0.86",
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
*which artefact replaced which* (via `based_on`, and for tasks via
`supersedes` and `superseded_by`)
but cannot answer *"is this the same item I approved last week, or a
different item with the same shape?"*, a question that arises in any
domain that does versioned work.

The Coordinator reads no field of a submitted artefact, `id` included.
To tell artefacts apart, for a second `review.request` on an open review
or for `approved_artefact_digest`, it compares the JCS content hash of
the whole artefact, so changing any field makes a different artefact.
Higher layers, analytics, dashboards, external indexes, can use
`logical_id` and `instance_id` to project the chain into a version
graph.

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

The records the reference Coordinators build themselves are the
override record (§9.4), the `snapshot`, whose `produced_by` is the
caller of `control.snapshot`, and the `route_decision` (§9.6).
`capture_fragment` depends on `capture.append`, which is specified and
not yet built.

Implementations MAY define additional kinds; the `schema` field MUST
reference a published JSON Schema for any non-standard kind.

### 9.4 Override artefacts

`decide.override` MUST carry `diff` and `rationale`, and MAY carry
`tags`, `policy_refs`, `logical_id`, `instance_id` and
`intent_preserved`:

```json
{
  "diff": [
    { "op": "replace", "path": "/content/text",
      "value": "I'm sorry for the delay. I've also waived shipping on your next order." }
  ],
  "rationale": "Compensation offered to retain customer per policy CSAT-3.",
  "tags": ["tone-adjustment", "compensation-offered"],
  "policy_refs": ["CSAT-3"],
  "logical_id": "lgl_01HZ9YX1A2B3C4D5E6F7G8H9J0",
  "intent_preserved": true
}
```

The Coordinator applies the diff to the artefact under review and
stores an override record of `id`, `task_id`, `reviewer`,
`based_on_artefact`, `diff`, `result` (the patched artefact),
`rationale`, `tags`, `policy_refs`, `ts` and the optional fields. The
diff is JSON Patch ([RFC 6902]): a `replace` MUST carry `value`, other
members on it are ignored, and a diff that cannot be applied is refused
with `-32012`. The rationale is free text; the tags are
workspace-defined categorisations useful for analysing override
patterns across time.

When the artefact under review carries a `logical_id`, the
override SHOULD carry the same `logical_id` and SHOULD set
`intent_preserved` to indicate whether the override changes the
underlying intent (`false`: this is a different decision) or
refines its expression (`true`: same decision, better delivery).
The field is informational; CHAP does not constrain semantics. It
exists because *"the human edited the agent's draft"* and *"the human
replaced the agent's draft with a different decision"* are
operationally different events that produce identical envelope
structures without it.

`control.supersede` replaces a task. Where the work is versioned, the
successor's artefacts SHOULD carry the same `logical_id` and set
`intent_preserved` accordingly.

An artefact MAY carry an optional `fulfils` field naming the `id` of the
decision it acts on, for example a tool call executed to carry out a
decision a human approved, so that an execution can be traced back to its
authorising decision. `fulfils` and `based_on` both express derivation but
differ in what they name and in what they prove. `based_on` names the input an
override was derived from and is reconstructible by replaying the chain;
`fulfils` names the authority an action claims and is **asserted by the
producer and not verified by the Coordinator**. An incorrect `fulfils`
identifier is a dangling reference that the chain still verifies, so it MUST
NOT be read as evidence of the same strength as `approved_artefact_digest`,
which the Coordinator checks and refuses on mismatch.

[RFC 6902]: https://www.rfc-editor.org/rfc/rfc6902

### 9.5 Routing hints on artefacts (optional)

An Artefact MAY carry an optional `routing_hints` object that
records production measurements: model confidence, model identifier,
cost incurred, latency. `review.depth` reads these signals when passed
as its `artefact_routing_hints` parameter, merged over the task's
`routing_hints`; `escalate.auto` reads the task's `routing_hints`
alone, and no method reads a `routing_hints` object inside an
artefact. The signals are recorded in the evidence envelope hash even
when the profile is not in use.

```json
{
  "routing_hints": {
    "confidence": "0.62",
    "model_id": "careful-draft-v2:2026-05",
    "cost_consumed_usd": "3.40",
    "latency_ms": 2810
  }
}
```

Fields:

| Field               | Type            | Constraint                                |
|---------------------|-----------------|-------------------------------------------|
| `confidence`        | decimal string  | in [0, 1]; model-specific calibration     |
| `model_id`          | string          | recommended whenever `confidence` is set  |
| `cost_consumed_usd` | decimal string  | non-negative                              |
| `latency_ms`        | integer         | non-negative                              |

`confidence` and `cost_consumed_usd` are fractional, so per §5.2 they are
carried as decimal strings (`"0.62"`, not `0.62`). A JSON number with a
fractional part is rejected at ingress.

**Calibration caveat.** Two `confidence: "0.83"` values from different
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
entries. Every accepted state-changing CHAP message produces exactly
one entry. The Coordinator also writes entries of its own, such as the
lapse of a whisper, recorded as a `notify.message` from
`service:coordinator`.

The read-only methods `workspace.describe`, `audit.read`,
`audit.verify_chain` and `audit.verify_receipt` are **not** recorded,
and neither is `audit.submit_to_scitt`, which reads the chain and sends
it to a transparency service. A chain that grew when it was read would
change the very state the read reports, verifying a chain would alter
the chain just verified, and a recorded submission would leave the
receipt attesting a chain one entry shorter than the log. Implementations
MUST NOT record these five methods, since a Coordinator that records
them produces a different chain for the same sequence of state changes
and so fails cross-implementation comparison.

**Refused calls.** A Coordinator MUST also record a refused call when it is
a governed attempt: the call is a JSON-RPC request or notification for a
method other than the five above, its `from` names a current member of an
existing workspace, and its refusal is none of the following.

- Malformed or invalid: `-32700`, `-32600` or `-32602`.
- A fault in the Coordinator: `-32603`.
- A signature or key code, `-32070` to `-32073`. Most of these mean the
  sender is not authenticated, so an entry in its name would attribute to it
  a call it may not have made. The same codes answer a key rotation or
  revocation that fails, and those are left off with them. `-32074`, a
  decision whose artefact digest does not match the artefact under review,
  is recorded.
- `-32601`, except where the profile gate (§15.4) refuses a method the
  catalogue marks `privileged`. That refusal is an attempt to pull an
  emergency brake the workspace has switched off, and is recorded. A
  `-32601` for a method that does not exist or is not implemented, or for an
  ordinary method the gate refuses, is not.

A refused `workspace.create` or `participant.join` is not recorded. These are
how a sender comes to be a member, and they run before the sender has a key
registered to check a signature against, so a refusal of either says nothing
reliable about who sent it. A request that cannot be canonicalised is not
recorded either, because it cannot be hashed. A Coordinator MUST NOT record
any other refusal: as with the reads, a Coordinator that recorded a different
set would produce a different chain for the same calls. A refusal entry holds
the request exactly as it arrived, signature included, under `request` rather
than `envelope`, with an `outcome` beside it:

```json
{
  "seq":      7,
  "arrived":  "2026-09-30T10:00:00.000Z",
  "request":  { /* the refused call, as received */ },
  "outcome":  { "status": "refused", "code": -32011 },
  "prev_hash": "sha256:..."
}
```

Holding the call under `request` keeps a reader that replays `envelope` from
treating the refusal as a call that took effect. The entry takes a `seq`,
`audit_count` counts it and `evidence_head` covers it. A chain written before
refusals were recorded holds none and reads as it always has.

**The order of checks.** Which refusal a call receives decides whether it is
recorded, so a Coordinator MUST make these checks in this order and answer
with the first that fails:

1. The request itself: a JSON-RPC 2.0 call with a non-empty string `method`,
   nested no deeper than 64 levels counting the envelope as the first, within
   the size limit, with `params` an object when present, for a method the
   Coordinator implements, with a canonical form (`-32600`, `-32601`,
   `-32602`).
2. Whether it is a signed copy of a recorded refusal (below).
3. The signature, where signatures are required (`-32070` to `-32073`).
4. Step-up freshness for a privileged method, where it is enforced
   (`-32402`).
5. The profile gate (§15.4).
6. The pause (`-32063`), for every method except `workspace.create`,
   `workspace.describe`, `audit.read`, `participant.join`,
   `participant.leave` and `control.resume`.
7. The method's own checks, including a `task.create` whose
   `idempotency_key` has been seen, which is answered with the task it
   created.

Where a caller who is not a member is refused makes no difference to the log,
since that refusal is never recorded. When a call fails more than one of its
method's own checks, this specification does not fix which it is answered
with, and two Coordinators can record different codes for it. The two
reference implementations make each method's checks in the same order.

**Signed copies.** A call is signed when it carries a top-level `sig` that is
a string, whether or not the Coordinator requires signatures. Every reader of the log
holds a copy of each signed call on it, signature included. Two signed calls
are the same call when what their senders signed, the call without its `sig`,
has the same canonical form, however the signature is encoded.

- A Coordinator MUST answer a signed call that is the same call as a recorded
  refusal with the recorded code and `data.refused_at_seq` set to that
  refusal's `seq`, and MUST NOT evaluate or record it again. Otherwise a
  refused request copied from the log could be sent once the reason for the
  refusal had passed, and take effect in its signer's name.
- A signed call that is the same call as an accepted entry is evaluated as
  any call is, but its refusal MUST NOT be recorded, since its signer made
  that call once.

A client that retries a refused call sends a new request with a new `id`, as
§4.1 requires of every message. An unsigned call is not compared with the
log: without a signature anyone can send any call in any name, and each of
its refusals is recorded.

Entries are linked by SHA-256 hashes over the canonical record and the
previous head:

```
entry_n.prev_hash = chain head before entry_n
chain_head        = SHA-256( JCS(record_n) || entry_n.prev_hash )
```

`record_n` is the recorded envelope of an accepted call, and the object
`{"outcome": outcome_n, "request": request_n}` of a refusal. An entry with no
outcome hashes exactly as it always has. Altering or removing either half of
a refusal breaks the chain, and moving the record under `envelope` leaves an
entry whose `envelope` is not a JSON-RPC call, which §10.2 reports as
malformed. Every digest is the string `sha256:` followed by 64 lowercase hex
characters. `JCS(record_n)` is the canonical serialisation of the record, and
`prev_hash` is concatenated as its full UTF-8 string form, prefix included.
The genesis entry's `prev_hash` is `sha256:` followed by 64 zeros.

The chain head, where there is one, is published in the workspace
descriptor as `evidence_head`, and the number of log entries as
`audit_count`.

### 10.2 Verification

Given the workspace's genesis entry and the current head, any verifier
can replay the chain and confirm:

1. Each message's signature verifies against the claimed `from`
   Participant's key as of `ts`.
2. Each `prev_hash` matches the recomputed previous entry hash.
3. Timestamps are monotonically non-decreasing.
4. No `id` is reused.

`audit.verify_chain` replays the chained entries of the log. It checks
item 2, and that each chained entry holds an accepted `envelope` alone, or a
refused `request` with an `outcome` of status `refused` and an integer code,
where the call it holds is a JSON-RPC 2.0 call with a string `method`. Any
other chained entry is reported as malformed. The reference implementations
do not check items 1, 3 and 4. Range parameters are declared on the request
but not honoured by either implementation, and are refused rather than
silently widened to the whole log.

**What verification does not establish.** A successful replay proves the
entries a verifier holds are internally consistent. It does not prove they
are **complete**. A chain that lost entries, and was then re-linked from an
earlier head, verifies exactly as a chain that never lost any: every
`prev_hash` matches, every signature verifies, and the result is `ok`.

This is inherent to a self-referential hash chain. Detecting absence needs a
witness outside the chain, which is what `audit-scitt/1.0` provides: a
transparency-service receipt for an entry is evidence it existed at a point
in time, independent of whatever the Coordinator now holds. Deployments that
need completeness and not merely integrity SHOULD anchor externally rather
than rely on `audit.verify_chain` alone.

The practical way to lose entries is to run more than one Coordinator
instance against a shared store; see the single-writer requirement in
§10.3.

**Coverage MUST be part of the verdict.** A workspace MAY enable chaining
part-way through its life, in which case entries written earlier carry no
`prev_hash` and lie outside the chain. Those entries are neither evidence of
tampering nor evidence of integrity: nothing was checked against them. A
verifier MUST NOT report a pass over a range it did not evaluate.

`audit.verify_chain` therefore returns one of three terminal outcomes, and
they are mutually exclusive:

| Outcome | Shape | Meaning |
|---|---|---|
| Broken | JSON-RPC error | The replay contradicted the stored evidence. |
| `not_evaluated` | `status: "not_evaluated"`, `ok: false`, `reason` | Part of the log lies outside coverage. |
| `verified` | `status: "verified"`, `ok: true` | Coverage is complete and the replay passed. |

`ok` MUST be `true` only when `status` is `verified`. A coverage count
reported beside a pass is not sufficient: the first reader under time
pressure takes the pass and does not read the count. Implementations MUST
report the two numbers `entries_checked` and `entries_unchecked`, which MUST
sum to `entries_total`, and `checked_from_seq`, the `seq` of the first
covered entry, or `null` when nothing was covered.

The one defined `reason` is `unchained_prefix`. Further reasons MAY be added
for other mechanisms; a reason is a refinement beneath the verdict and never
a modifier on it.

A workspace that never enabled chaining is a different case and is refused
outright, because there is no chain to ask about.

### 10.3 Single-writer requirement

A Coordinator is a **single-writer** component. Its dispatch is serial, it
holds workspace state in memory, and it computes each chain link from the
head it holds. The store interface is a persistence mechanism, not a
concurrency-control mechanism: `Store.save` is an unconditional write.

Running two or more Coordinator instances against one shared store is
therefore **not supported**. A stale instance's write replaces a newer one
wholesale, so entries written by the other instance are lost, the chain is
re-linked from the surviving instance's head, and `audit.verify_chain`
reports `ok` on the result. The loss is silent and CHAP's own verification
will not reveal it.

Deployments needing more than one instance MUST serialise writes above the
Coordinator, by partitioning workspaces across instances so that no
workspace is ever written by two, or by an external lock. Enforcing this in
the protocol, by making `Store.save` a compare-and-swap and dispatch
re-runnable against reloaded state, is a larger change than it appears and
is left to a later milestone.

### 10.4 Checkpoints

The Coordinator SHOULD emit periodic **checkpoint** entries
(default: every 1000 entries) signed with its long-lived key. A
checkpoint is a notification with method `audit.checkpoint` whose
params include the current head, the entry count, and the
Coordinator's signature over both. Verifiers MAY anchor checkpoints
to external transparency logs.

### 10.5 External anchoring

For deployments requiring stronger tamper-evidence, workspaces MAY
periodically publish their chain head to:

- An internal append-only store with separate access controls.
- A transparency log (e.g. a Trillian-style Merkle log).
- A third-party notarisation service.

Anchoring is referenced from the workspace descriptor's `anchors[]`
array; the format of each anchor reference is anchor-specific. Neither
reference records anchors or returns `anchors[]` from
`workspace.describe`; `audit.submit_to_scitt` hands its statements or
receipts to the caller.

### 10.6 Retention and redaction

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
  observers or a percentage of traffic). Where the workspace advertises
  `modes/1.0`, every `trial` task requires review.
- **`production`**: Output reaches its intended audience with full
  effect.

### 11.2 Promotion

Modes form a strict order: `shadow < trial < production`. Promotion
moves a workspace or task forward in this order; demotion moves it
back. Both transitions are recorded as evidence entries.

A workspace declares a `mode_ceiling` that bounds the maximum mode
its tasks may carry. `control.set_mode_ceiling` changes the ceiling in
either direction. Neither reference checks a role or a policy entry
for it, so any member may call it, subject to step-up where that is
enforced (§11.3).

### 11.3 Enforcement

The Coordinator MUST:

- Reject any `task.create` or `control.supersede` whose mode exceeds the
  workspace's ceiling
  with error `-32040` (`mode_ceiling_exceeded`).
- Record every mode change as a first-class evidence entry.
- Reject a privileged method, `control.set_mode_ceiling` among them,
  with `-32402` (`step_up_required`) where the Coordinator enforces
  step-up and the caller is a human member, or a member with an OIDC
  binding, whose `auth_time` is older than `step_up_window_sec`.
  Step-up is a Coordinator option, separate from the advertised
  profiles (§6.5).

The deployment's delivery layer filters delivery of shadow-mode output
to a `shadow_observers` list the deployment keeps, since neither
reference stores one. The Coordinator answers the caller who asked it.
See §15.1.

### 11.4 Per-task overrides

A task carries its own mode, set at `task.create`, where it defaults to
the workspace's mode, or on a `control.supersede` successor, where it
defaults to the mode of the task replaced. It MAY be lower or higher
than the workspace's mode (e.g. running a single task in `shadow`
inside an otherwise `production` workspace, for debugging) and MUST NOT
exceed the `mode_ceiling` in force when it is created. `escalate.raise`
gives the new task the mode `new_task.mode` names, or else the mode of the
task it escalates, and refuses the escalation with `-32040` when that mode
is above the ceiling in force.

---

## 12. Methods

This section enumerates the method catalogue. The authoritative
machine-readable form is [`schemas/profiles/chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json).

Every method has:

- A **namespace** (`workspace`, `participant`, `task`, etc.).
- A **type** (`request`, `response` or `notification`).
- A list of **required scopes** for the caller.
- A **privileged** flag indicating whether step-up auth is required.
- A **status**: `implemented` where a reference provides it, `spec-only`
  where it is specified and not yet built, and `reserved` for the
  assignment lifecycle of §12.3.

### 12.1 `workspace.*`

| Method                  | Type         | Privileged | Status      | Description                                |
|-------------------------|--------------|------------|-------------|--------------------------------------------|
| `workspace.create`      | request      | no         | implemented | Create a new workspace.                    |
| `workspace.describe`    | request      | no         | implemented | Return the workspace descriptor.           |
| `workspace.set_profiles`| request      | yes        | implemented | Replace the advertised profile list. Admin only. |
| `workspace.invite`      | request      | yes        | spec-only   | Invite a Participant.                      |
| `workspace.evict`       | request      | yes        | spec-only   | Remove a Participant.                      |
| `workspace.set_mode`    | request      | yes (for promotions towards production) | spec-only | Change the workspace mode. |
| `workspace.pause`       | request      | yes        | spec-only   | Suspend new task acceptance.               |
| `workspace.resume`      | request      | yes        | spec-only   | Resume operation.                          |
| `workspace.close`       | request      | yes        | spec-only   | Seal the workspace.                        |

### 12.2 `participant.*`

| Method                  | Type         | Privileged | Status      | Description                                |
|-------------------------|--------------|------------|-------------|--------------------------------------------|
| `participant.join`      | request      | no         | implemented | Join a workspace, creating it if absent.   |
| `participant.leave`     | request      | no         | implemented | Leave the workspace.                       |
| `participant.describe`  | request      | no         | spec-only   | Return a Participant's descriptor.         |
| `participant.announce`  | notification | no         | spec-only   | A Participant announces presence/availability. |
| `participant.heartbeat` | notification | no         | spec-only   | Periodic liveness signal.                  |
| `participant.rotate_key`| request      | yes        | implemented | Replace signing key (signed with old key). |
| `participant.revoke_key`| request      | yes        | implemented | Mark a key compromised. Revoking another member's key requires the admin role. |

### 12.3 `task.*`

| Method               | Type         | Privileged | Status     | Description                                   |
|----------------------|--------------|------------|------------|-----------------------------------------------|
| `task.create`        | request      | no         | implemented | Open a task and assign it.                    |
| `task.update`        | request      | no         | implemented | Move a task between states; carries an optional progress note. |
| `task.complete`      | request      | no         | implemented | Submit a completed task with its artefact.    |
| `task.assign`        | request      | no         | reserved   | Propose a task to an assignee.                |
| `task.accept`        | response     | no         | reserved   | Accept an assignment.                         |
| `task.decline`       | response     | no         | reserved   | Decline an assignment.                        |
| `task.start`         | notification | no         | reserved   | The assignee has begun work.                  |
| `task.progress`      | notification | no         | reserved   | Progress update.                              |
| `task.describe`      | request      | no         | reserved   | Return a task's current state.                |

The six reserved methods belong to a finer-grained assignment lifecycle that
no implementation provides. A conforming implementation MUST NOT be expected
to answer them, and the state machine in §8.1 does not use them.
`task.create` accepts an optional
`idempotency_key`: a repeat carrying a key already seen in the workspace returns
the original task rather than creating (or recording) a duplicate, so an
at-least-once transport can retry safely. The key is not part of the task
descriptor; the Coordinator keeps a bounded per-workspace map of recent keys.

### 12.4 `review.*`

| Method               | Type         | Privileged | Status      | Description                                   |
|----------------------|--------------|------------|-------------|-----------------------------------------------|
| `review.request`     | request      | no         | implemented | Ask one or more reviewers to evaluate an artefact. |
| `review.acknowledge` | notification | no         | spec-only   | Reviewer signals they have begun review.      |

### 12.5 `decide.*` / `abstain.*` / `escalate.*`

| Method               | Type         | Privileged | Status      | Description                                   |
|----------------------|--------------|------------|-------------|-----------------------------------------------|
| `decide.approve`     | request      | no         | implemented | Approve a draft as-is.                        |
| `decide.reject`      | request      | no         | implemented | Reject a draft with a reason.                 |
| `decide.override`    | request      | no         | implemented | Approve a modified version; produces an override artefact. |
| `abstain.declare`    | request      | no         | implemented | Decline to decide; flags for escalation.      |
| `escalate.raise`     | request      | no         | implemented | Hand a task up the chain with context.        |

### 12.6 `whisper.*` / `capture.*`

| Method               | Type         | Privileged | Status      | Description                                   |
|----------------------|--------------|------------|-------------|-----------------------------------------------|
| `whisper.ask`        | request      | no         | implemented | Quick, deadline-bound interrupt question.     |
| `whisper.answer`     | request      | no         | implemented | Answer a whisper.                             |
| `capture.append`     | request      | no         | spec-only   | Append an ad-hoc fragment (note, tag, link) to a task. |

### 12.7 `handoff.*`

| Method               | Type         | Privileged | Status      | Description                                   |
|----------------------|--------------|------------|-------------|-----------------------------------------------|
| `handoff.propose`    | request      | no         | implemented | Propose transferring work to another participant. |
| `handoff.accept`     | request      | no         | implemented | Accept a handoff.                             |
| `handoff.decline`    | request      | no         | implemented | Decline a handoff.                            |

### 12.7a `routing.*` (profile `routing/1.0`)

| Method            | Type    | Privileged | Status      | Description                                       |
|-------------------|---------|------------|-------------|---------------------------------------------------|
| `task.route`      | request | no         | implemented | Pick an assignee from candidates given `routing_hints`. Produces a `route_decision` artefact. |
| `review.depth`    | request | no         | implemented | Decide review depth (`skip` / `spot_check` / `full`). Produces a `route_decision` artefact. |
| `escalate.auto`   | request | no         | implemented | Evaluate auto-escalation rules and report the target when one fires. The task is unchanged. |

These methods are only present when the workspace advertises
`routing/1.0` in `workspace.describe.profiles`. They read the optional
`routing_hints` on Tasks (§8.4), and `review.depth` also takes the
artefact signals of §9.5 as a parameter. Each records its decision as
a `route_decision` artefact, held in the workspace under the identifier
returned to the caller. The evidence chain holds the request; the
selected assignee, review depth and escalation target are not written
to it. The full profile is specified in
[`profiles/routing.md`](./profiles/routing.md).

### 12.8 `notify.*`

| Method               | Type         | Privileged | Status      | Description                                   |
|----------------------|--------------|------------|-------------|-----------------------------------------------|
| `notify.message`     | notification | no         | spec-only   | Generic free-text message between participants. |
| `notify.alert`       | notification | no         | spec-only   | High-priority alert with a severity field.    |

### 12.9 `deliberate.*`

| Method               | Type         | Privileged | Status      | Description                                   |
|----------------------|--------------|------------|-------------|-----------------------------------------------|
| `deliberate.open`    | request      | no         | implemented | Open a multi-party thread with a decision rule. |
| `deliberate.comment` | notification | no         | implemented | Add a comment to an open deliberation.        |
| `deliberate.vote`    | request      | no         | implemented | Cast a vote (yea/nay/abstain, optional weight). |
| `deliberate.close`   | request      | no         | implemented | Close the deliberation; computes the outcome per rule. |

### 12.10 `control.*`

| Method               | Type         | Privileged | Status      | Description                                   |
|----------------------|--------------|------------|-------------|-----------------------------------------------|
| `control.pause`      | request      | yes        | implemented | Pause a task, a participant or the whole workspace (`scope`). |
| `control.resume`     | request      | yes        | implemented | Resume a paused task, participant or workspace (`scope`). |
| `control.cancel`     | request      | yes        | implemented | Cancel a task (terminal).                     |
| `control.supersede`  | request      | yes        | implemented | Replace a task with another (terminal).       |
| `control.snapshot`   | request      | yes        | implemented | Produce a workspace snapshot artefact.        |
| `control.rollback`   | request      | yes        | implemented | Roll back to a prior snapshot.                |
| `control.set_mode_ceiling` | request | yes       | implemented | Change the workspace's mode ceiling.          |

### 12.11 `audit.*`

| Method               | Type         | Privileged | Status      | Description                                   |
|----------------------|--------------|------------|-------------|-----------------------------------------------|
| `audit.read`         | request      | no         | implemented | Read a range of evidence entries, accepted and refused (§10.1). |
| `audit.verify_chain` | request      | no         | implemented | Replay the hash chain and report coverage (§10.2). |
| `audit.submit_to_scitt` | request   | no         | implemented | Build SCITT statements, and submit them where a submitter is configured. |
| `audit.verify_receipt` | request    | no         | implemented | Verify a SCITT receipt through the configured hook. |
| `audit.verify`       | request      | no         | spec-only   | Verify the chain over a range.                |
| `audit.checkpoint`   | notification | no         | spec-only   | Coordinator-emitted checkpoint.               |
| `audit.redact`       | request      | yes        | spec-only   | Redact a prior entry (preserves hash).        |
| `audit.export`       | request      | yes        | spec-only   | Export the chain in a portable format.        |

---

## 13. Error model

### 13.1 Error object

Error responses carry an `error` object:

```json
{
  "code": -32402,
  "message": "Step-up authentication required",
  "data": { "window_sec": 300, "age_sec": 1200 }
}
```

### 13.2 Error code ranges

Error codes follow JSON-RPC conventions with CHAP-specific extensions:

| Range                      | Meaning                                     |
|----------------------------|---------------------------------------------|
| -32700, -32600 to -32603   | JSON-RPC 2.0 standard errors                |
| -32010 to -32099           | Profile errors, one decade per profile      |
| -32400 to -32499           | Identity profile errors                     |
| -32500 to -32599           | Routing and policy errors                   |
| -32900 to -32999           | Implementation-defined                      |

The JSON-RPC band is fixed by that specification and CHAP does not reuse it.
Each profile owns one decade, and a code identifies the profile that
allocated it. Core methods also return codes from profile decades:
`-32011`, `-32040` and `-32063` from `review/1.0`, `modes/1.0` and
`control/1.0`, the `security-signed/1.0` codes from the key methods,
and the identity codes from `participant.join`. The decades are:

| Decade   | Profile              | Decade   | Profile              |
|----------|----------------------|----------|----------------------|
| -3201x   | `review/1.0`         | -3206x   | `control/1.0`        |
| -3202x   | `whisper/1.0`        | -3207x   | `security-signed/1.0`|
| -3203x   | `deliberation/1.0`   | -3208x   | `audit-scitt/1.0`    |
| -3204x   | `modes/1.0`          | -3240x   | `identity-oidc/1.0`  |
| -3205x   | `handoff/1.0`        | -3241x   | `identity-vc/1.0`    |
|          |                      | -3251x   | `routing/1.0`        |

### 13.3 Standard error codes

The five JSON-RPC 2.0 codes carry their standard meanings:

| Code   | Symbol             | Meaning                                     |
|--------|--------------------|---------------------------------------------|
| -32700 | `parse_error`      | Invalid JSON.                               |
| -32600 | `invalid_request`  | Not a JSON-RPC 2.0 call with a non-empty string `method`, or nested deeper than 64 levels, or larger than `max_envelope_bytes`. |
| -32601 | `method_not_found` | Unknown method, or one whose owning profile the workspace does not advertise (§15.4). |
| -32602 | `invalid_params`   | Params missing or ill-typed, or a call the current state rules out: unknown workspace or task, illegal transition, terminal task, broken chain. Never recorded (§10.1). |
| -32603 | `internal_error`   | Implementation defect.                      |

Every other code belongs to the profile that defines it, and **each
profile's own error table is authoritative**: `profiles/review.md` §5,
`profiles/whisper.md` §6, `profiles/deliberation.md` §5,
`profiles/modes.md` §6, `profiles/handoff.md` §6, `profiles/control.md` §6,
`profiles/security-signed.md` §7, `profiles/audit-scitt.md` §8,
`profiles/identity-oidc.md` §8, `profiles/identity-vc.md` §8, and
`profiles/routing.md` §3 to §5. The decade map in §13.2 says which profile a
code belongs to.

Codes are not restated here, so each profile's table stays the one
registry for its codes.

Implementations MUST use the codes their profiles define, MUST NOT reuse the
JSON-RPC band, and MAY define implementation-specific codes in the -32900
range.

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
serves JSON-RPC on `POST /rpc` and a demonstration stream on
`GET /events`; it does not implement this binding.

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

This section is grouped by who does the work: the Coordinator, a profile it
advertises, or the deployment.

**A conformant Coordinator MUST:**

1. Order the chain by acceptance rather than by the sender's clock: record
   arrival, assign a sequence, and link each entry to the one before. A
   sender-declared timestamp that goes backwards is an operational signal,
   described in
   [SECURITY.md](./SECURITY.md#sender-declared-timestamps).
2. Record every accepted operation on the chain, and the refused calls §10.1
   names. The five methods §10.1 names as unrecorded are the exception,
   because appending on read would grow and re-link the chain each time it
   was inspected.
3. Refuse a method whose owning profile the workspace does not advertise
   (§15.4). At `workspace.create`, add `security-signed/1.0` or
   `identity-oidc/1.0` where the Coordinator enforces it and the request
   omits it, and refuse a request advertising either while the
   Coordinator does not enforce it (§6.5).
4. Enforce the authorisation the workspace holds: membership where a profile
   requires it, and the role checks the method defines. A `required_scope` is
   declared per method in the catalogue and is not yet enforced by either
   reference; treat it as descriptive until it is.
5. Enforce the mode ceiling, refusing a `task.create` or `control.supersede`
   above it with `-32040`, and record every mode change on the chain.
6. Generate `id` values as cryptographically random ULIDs outside test mode.

**A profile turns these on, and §6.5 binds advertising to enforcing at
`workspace.create`:**

7. `security-signed/1.0`: verify every signature before accepting a message
   into the chain, and refuse a message that does not verify.
   `workspace.create` and `participant.join` are exempt, since they run
   before the sender has a registered key.
8. `identity-oidc/1.0`: verify the OIDC token a `participant.join`
   presents, refuse one that does not verify (`-32403`), refuse one that
   does not bind to the joining participant (`-32404`, §5.4), and pin
   its `cnf.jwk` as the member's key.

Step-up is a Coordinator option, independent of the advertised set
(§6.5); where it is on, a human member, or one with an OIDC binding,
whose `auth_time` is older than `step_up_window_sec` is refused a
privileged method with `-32402`.

**The deployment MUST**, because a Coordinator library has no transport or
delivery layer of its own to do it in:

9. Use TLS 1.3 or later for every production transport.
10. Filter delivery of shadow-mode output to a `shadow_observers` list the
    deployment keeps, since neither reference stores one. A Coordinator
    answers the caller who asked; which participants are notified of what
    is the delivery layer's decision, and no reference implements a
    delivery layer.

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

Encrypted log content is planned as a Draft profile after 1.0
([ROADMAP.md](./ROADMAP.md)).

### 15.4 Threat model

This section identifies adversaries CHAP defends against, adversaries
it does not, and the protocol-level countermeasures behind each
defended class. The full operational threat model is in
[SECURITY.md](./SECURITY.md).

**Replay.** An adversary captures a previously-valid envelope and
re-injects it into the chain. *Countermeasures:* each entry carries
the Coordinator's own arrival time and sequence, whatever the sender's
`ts` says. A signed copy of a recorded refusal is answered with that
refusal (§10.1), and a `task.create` repeating a seen `idempotency_key`
returns the original task. Any other copy of an accepted call is
evaluated as a new call and, if still valid, takes effect and is
recorded again. Rejecting an envelope `id` on second observation is a
deployment-level defence, described in
[SECURITY.md](./SECURITY.md#envelope-id-replay): neither reference
keeps a seen-id set.

**Downgrade.** An adversary forces capability negotiation in
`workspace.describe` to advertise fewer profiles than both peers
support, hoping to suppress a defensive profile (e.g.
`security-signed/1.0` or `audit-scitt/1.0`). *Countermeasures:* the
advertised set is on the log: the `workspace.create` call sets the
first list, and each change is a recorded `workspace.set_profiles`
call, which needs the admin role. `workspace.create` is never
signature-checked, and `workspace.set_profiles` can narrow or widen the
set without the checks §6.5 applies at creation, so a relying party
SHOULD read the set's history from the log. Deployments concerned about
downgrade SHOULD treat the profile set as policy: any participant whose
`participant.join` declares a lower profile set than the workspace's
mandatory minimum MUST be refused.

**Capability confusion across profiles.** Two profiles define methods
with similar names but different security properties (for example,
`decide.override` in `review/1.0` versus a hypothetical
`decide.override` in a forked profile). *Countermeasures:* methods are
namespaced (`namespace.verb`), and **a Coordinator MUST refuse a method
whose owning profile is not in the workspace's advertised set**, except
for Core methods and the reads named in §6.5.

The rule is per method. It cannot be written per namespace, because
several namespaces span profiles: `workspace` covers Core, `modes` and
`control`; `task`, `review` and `escalate` each straddle their home
profile and `routing`; `audit` covers Core and `audit-scitt`. Which
profile owns which method is declared in
[`chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json),
by the `since` field on each entry.

The refusal is `-32601` with the message an unknown method receives.
Both references add `data: {"profile": …, "advertised": [...]}` to it,
and nothing to an unknown method, so any caller can tell the two apart.
When the gate refuses a privileged method, a member's attempt is also
recorded (§10.1).

**Key rotation.** A participant rotates a signing key mid-chain.
*Countermeasures:* key rotation is the Core method
`participant.rotate_key`, naming `old_kid` and `new_jwk`. Where
signatures are required it MUST be signed with the old key, and is
refused with `-32073` otherwise. Verifiers walking the chain MUST treat
the post-rotation entries as signed by the new key only after the
rotation event itself has been verified by the old key. A rotation
event MUST NOT retroactively re-sign earlier entries.

**Evidence-chain forking under partition.** Two Coordinators serving
the same workspace under a network partition each accept envelopes
into their local chain head; on partition heal the chains have
diverged. *Countermeasures:* CHAP's evidence chain is per-workspace
and per-Coordinator; the protocol does not provide a Byzantine fault
tolerant consensus layer. Deployments that require continuity through
partition MUST run a single logical Coordinator with HA replication
that preserves chain linearity. Neither the chain nor
`audit.verify_chain` detects a fork: each branch verifies on its own.
Deployments SHOULD anchor chain heads to an external transparency log
via `audit-scitt/1.0` to make fork detection independent of the
Coordinators themselves.

**Compromised Coordinator.** An adversary controls a Coordinator and
attempts to forge entries, suppress entries, or rewrite history.
*Countermeasures:* participant signatures exist only under
`security-signed/1.0`. Where it is in force, each call is signed by the
participant that sends it, so the Coordinator cannot forge a call from
a participant whose key it does not hold, for a verifier that knows the
members' keys from outside the Coordinator, which keeps the key list
itself. Even then `workspace.create` and `participant.join` are never
verified, so a compromised Coordinator can forge joins. A participant
that keeps its own record of what it sent can show that a call was
suppressed. A Coordinator that rewrites history can recompute every
`prev_hash` and the head (§10.2), so only a head published or anchored
outside its control, such as a SCITT receipt's witnessed root, shows the
rewrite. A Coordinator that is the
sole signer of receipts can equivocate; deployments defending against
this MUST use `audit-scitt/1.0` with an externally operated
transparency service whose witnesses are not under the same
administrative control as the Coordinator.

**Identity confusion.** A participant adopts a Participant URI that
resembles another's. *Countermeasures:* Participant URIs in
`human:`, `agent:`, `service:` namespaces MUST be bound to a verified
identity (OIDC subject claim or VC subject DID) before being
admitted to a workspace via `participant.join`. The binding is
recorded in the participant descriptor and signed. Neither reference
requires a verified identity at a first join or signs a descriptor;
until milestone 0.6, deployments authenticate joins at the transport.

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
artefact it produces. The reference helpers (`wrapMcpToolCall`,
`wrap_mcp_tool_call`) cite the call with kind `mcp_tool_call`, `server`
(an identifier the caller supplies), `tool`, `input_hash` and
`output_hash`, each SHA-256 over the JCS form. They also place the
arguments and result in the task, so both bodies enter the evidence
chain beside the hashes. A deployment that must keep bodies off the
chain writes its own citation and omits them.

Where the bodies are kept off the chain, a verifier with access to the
MCP server's audit log can reconstruct the full input and output and
confirm they match the hashes; a verifier without that access still
has cryptographic proof of *which* tool was called and that the
recorded inputs and outputs have not been altered.

See [`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md)
for the full pattern.

### 16.2 A2A composition

When work crosses an organisational boundary, an **A2A bridge
service** participates in both protocols. Inside the local CHAP
workspace, the bridge appears as `service:bridge@example.org`. It
accepts CHAP tasks, forwards them over A2A, returns the result as a
CHAP artefact, and cites `remote_agent`, `sent_hash` and
`received_hash`. The reference helpers (`wrapA2aMessageExchange`,
`wrap_a2a_message_exchange`) record both messages and no correlation
IDs.

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
MCP **2026-07-28** while continuing to serve MCP **2025-11-25**
clients, A2A **0.3.0** (via the TypeScript SDK), and A2A **1.0** (via
the Python SDK). See the implementation notes in
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
  `task.create`, `task.update`, `task.complete`, `review.request`,
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
independently authored implementation. The Python coordinator and a
standalone TypeScript server pass the harness. The two coordinators in
this repository share authorship, so no implementation can correctly
claim the Full level under this revision. Implementations
already meeting the technical requirements above are welcome to
publish a Recommended attestation and a list of additional methods
implemented; promotion to Full will be opened once the interop
substrate is in place.

### 17.2 Self-attestation

Implementations MAY self-attest a conformance level by publishing a
conformance statement listing the implemented methods, transports,
and protections. See [`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md)
for the template. The attestation MUST state which methods are
implemented and which are only declared; consumers SHOULD treat a method
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
