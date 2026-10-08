# CHAP's Relationship to Other Standards

A draft protocol that doesn't engage with the standards it overlaps
will be (rightly) dismissed as reinvention. This document maps every
CHAP concept to its nearest existing standard and states whether CHAP
reuses, profiles, or diverges from it, and why.

If you're reviewing CHAP's design, **read this document first**.
Many concerns about "you should just use X" are answered here.

---

## 1. Summary table

| CHAP concept                  | Existing standard                                | Relationship |
|------------------------------|--------------------------------------------------|--------------|
| Envelope format              | **JSON-RPC 2.0**                                 | Reuses verbatim. |
| Canonical bytes for hashing  | **RFC 8785 (JCS)**                               | Reuses verbatim. |
| Override diff                | **RFC 6902 (JSON Patch)**                        | Reuses verbatim. |
| Identity for humans          | **OIDC + DPoP (RFC 9449)** or **cnf.jwk (RFC 7800)** | Reuses via `identity-oidc` profile. |
| Richer identity claims       | **W3C Verifiable Credentials 2.0**               | Reuses via `identity-vc` profile. |
| Identity for services        | **SPIFFE / SPIRE**                               | Recommended deployment pattern. |
| Audit / transparency log     | **SCITT architecture (RFC 9943)**                | Outside witness via the `audit-scitt` profile; CHAP keeps its own log and chain. |
| Audit statement format       | **COSE (RFC 9052) + SCITT receipts**             | `audit-scitt` builds COSE_Sign1-shaped statements; the deployment's submitter signs them. |
| Tool calls inside artefacts  | **MCP**                                          | Composed (cited outward; also exposed inward via `coordinator-mcp` adapter). |
| Cross-org peer delegation    | **A2A**                                          | Composed (bridge participant outward; also exposed inward via `coordinator-a2a` adapter). |
| Federation between workspaces | **ActivityPub** (Actors, Inbox/Outbox)          | A possible mapping, which no profile defines. |
| Participant lifecycle (provision/deprovision) | **SCIM 2.0**                  | A possible mapping, which no profile defines. |
| Transport                    | **HTTP**                                         | HTTP POST, required by Core; MCP and A2A through the adapters; others specified and unbuilt. |
| URIs                         | **RFC 3986**                                     | Plain URIs; CHAP defines the scheme grammar. |
| Versioning                   | **Semantic Versioning 2.0**                      | From 1.0; before 1.0 a minor release may break things. |
| Conformance attestations     | **in-toto attestation framework**                | The harness writes an in-toto Statement (`--attest`). |

The only things CHAP introduces that don't exist elsewhere are
**the methods themselves** (`task.create`, `review.request`,
`decide.override`, `abstain.declare`, `whisper.ask`, etc.), the
**override-with-rationale shape** that turns human edits into
structured learning signals, and its own audit log with an optional
hash chain. Everything else is plumbing.

---

## 2. Envelopes: JSON-RPC 2.0

CHAP envelopes are valid [JSON-RPC 2.0](https://www.jsonrpc.org/specification)
messages. Specifically:

| CHAP field        | JSON-RPC equivalent             |
|------------------|---------------------------------|
| `id`             | `id`                            |
| `method`         | `method`                        |
| `params`         | `params`                        |
| `result`         | `result`                        |
| `error`          | `error` (with code/message/data) |

CHAP puts its own fields (`workspace`, `from`, `to`, `ts`) inside
`params`, and `security-signed/1.0` adds a top-level `sig`
([`core/SPEC.md`](./core/SPEC.md) §2). SPECIFICATION.md §4 describes
another shape, reconciled in milestone 0.4.

**Why JSON-RPC** and not, say, gRPC or a custom format? JSON-RPC is
trivial to implement in any language, terse on the wire, well-known
to operators, and what MCP already uses. Compatibility with MCP's
envelope shape means tooling (debuggers, proxies, gateways) can be
shared.

A CHAP envelope from any conformant implementation passes a generic
JSON-RPC 2.0 validator. The extra CHAP-specific fields are present
under their own names.

---

## 3. Canonical bytes: RFC 8785 (JCS)

When messages must be hashed or signed, CHAP canonicalises them with
[JSON Canonicalization Scheme (RFC 8785)](https://datatracker.ietf.org/doc/html/rfc8785).
Both coordinators canonicalise every request, to measure it against
the size limit and to refuse one that has no canonical form. Signing,
the chain and artefact digests hash the same canonical bytes.

JCS was chosen because:

1. It's an IETF standard.
2. It produces deterministic bytes from any conformant JSON parser.
3. It has reference implementations in every common language.

CHAP does not define its own canonicalisation rules.

---

## 4. Override diffs: RFC 6902 (JSON Patch)

The `decide.override` method carries a `diff` field whose value is
an [RFC 6902 JSON Patch](https://datatracker.ietf.org/doc/html/rfc6902)
document. Worked example: [`examples/05-override-capture.md`](./examples/05-override-capture.md).

Why JSON Patch and not a custom diff format? Universal tooling, every
language has a library, every implementation can apply or invert the
patch deterministically.

The CHAP innovation isn't the diff itself, it's the **rationale +
tags + policy_refs** carried alongside the diff. Those three fields
turn an opaque edit into structured learning data.

---

## 5. Identity: OIDC, DPoP, W3C VC, SPIFFE

CHAP has no native identity layer. It defines two profiles, each of
which fully delegates to an existing standard.

### 5.1 `identity-oidc` (recommended for humans)

A human Participant's identity is asserted by an [OIDC](https://openid.net/specs/openid-connect-core-1_0.html)
ID token whose `cnf.jwk` claim ([RFC 7800](https://datatracker.ietf.org/doc/html/rfc7800))
binds the CHAP signing key. Step-up authentication uses standard
OIDC `auth_time` + `prompt=login`. Refresh uses standard OIDC
refresh tokens.

Implementations of this profile do **not** invent identity flows;
they call out to the org's IdP (Okta, Auth0, Keycloak, Azure AD,
Google Identity, etc.) the same way every other web app does.

DPoP ([RFC 9449](https://datatracker.ietf.org/doc/html/rfc9449)) is
the recommended way to bind tokens to signing keys when the IdP
doesn't natively support `cnf.jwk`.

### 5.2 `identity-vc` (recommended for richer claims)

When stronger or richer identity is needed, for example, a human's
attested clinical-credentialing role, a regulatory licence number, or
a cross-organisation credential, the human's identity is a
[W3C Verifiable Credential 2.0](https://www.w3.org/TR/vc-data-model-2.0/)
presented during the participant handshake.

A VC can carry arbitrary structured claims signed by an issuer the
workspace trusts (a regulator, an employer, a professional body).
A verifier supplied by the deployment checks the VC at
`participant.join`, and the Coordinator records the holder it returns
in the participant descriptor.

This is strictly more expressive than OIDC `cnf.jwk` but also more
complex; pick OIDC for typical SaaS deployments and VC when richer
claims actually matter.

### 5.3 SPIFFE for services

Agents and services in a service mesh authenticate with [SPIFFE](https://spiffe.io)
SVIDs. The CHAP signing key is bound to the SPIFFE ID via the mesh's
workload identity infrastructure.

---

## 6. Audit / transparency log: SCITT

CHAP keeps its own log and, with the chain on, its own hash chain
([SPECIFICATION.md §10.1](./SPECIFICATION.md#101-evidence-chain)),
checked by `audit.verify_chain`. `audit-scitt/1.0` adds an outside
witness under the SCITT architecture
([RFC 9943](https://www.rfc-editor.org/rfc/rfc9943)):
`audit.submit_to_scitt` builds a statement per entry for a submitter
the deployment supplies, which signs and registers it, and
`audit.verify_receipt` calls a deployment verifier. Neither coordinator
runs a transparency service. SCITT signs COSE
([RFC 9052](https://datatracker.ietf.org/doc/html/rfc9052)) structures,
which are CBOR.

For Core-only deployments that don't need cryptographic audit, the
log can be a plain database table, with no SCITT involvement and no
crypto. The profile is opt-in.

See [`profiles/audit-scitt.md`](./profiles/audit-scitt.md).

---

## 7. Composition with MCP and A2A

CHAP composes with MCP and A2A in **two directions**: by citation
(outward) and by transport adapter (inward).

### 7.1 By citation (outward)

The original composition pattern. An MCP tool call or A2A exchange
that happens during CHAP work is recorded inside the workspace as
structured evidence, without bringing the external traffic onto the
CHAP wire:

- An MCP tool call called by an agent during CHAP work becomes a
  `citation` of kind `mcp_tool_call` inside the agent's CHAP
  artefact, with `input_hash` and `output_hash` providing the
  hash boundary. See [`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md).
- An A2A peer is represented inside a CHAP workspace by a
  `service:bridge…` Participant. Cross-org work goes via that
  Participant; the A2A traffic does not cross the CHAP wire. See
  [`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

Library helpers `wrapMcpToolCall` / `wrap_mcp_tool_call` and
`wrapA2aMessageExchange` / `wrap_a2a_message_exchange` make the
citation a one-liner from either reference implementation.

### 7.2 By transport adapter (inward)

A CHAP Coordinator can also present itself **as** an MCP server or
an A2A agent, with every CHAP method exposed as a tool or skill.
This lets existing MCP clients and A2A orchestrators drive a CHAP
workspace without any CHAP-specific code:

- `@brightbeamai/chap-coordinator-mcp` (TypeScript) and
  `chap_coordinator.transports.mcp_server` (Python) target MCP
  **2026-07-28** and serve MCP **2025-11-25** clients as well. Every
  method the coordinators implement becomes an MCP tool named
  `chap.<method>`. Reference stdio servers ship in
  `reference/mcp-server-{ts,py}/`.
- `@brightbeamai/chap-coordinator-a2a` (TypeScript) and
  `chap_coordinator.transports.a2a_server` (Python) expose the
  Coordinator as an A2A agent. The same methods become discrete
  `AgentSkill` entries. The TypeScript adapter speaks A2A 0.3 and the
  Python adapter A2A 1.0. Milestone 0.5 moves the TypeScript adapter
  to A2A 1.0. Reference HTTP servers ship in
  `reference/a2a-server-{ts,py}/`.

### 7.3 Composition stacks

The two directions stack. A workspace can be driven over MCP from
Claude Desktop while its agents call other MCP servers for tools
and cite those calls; the same workspace can be exposed as an A2A
agent to an Azure orchestrator while bridging to A2A peers
externally. Same wire formats, different roles per protocol.

The MCP and A2A protocols evolve on their own timelines, and each
adapter pins the revision it speaks. The TypeScript adapter speaks
A2A 0.3 and the Python adapter A2A 1.0. Milestone 0.5 moves the
TypeScript adapter to A2A 1.0.

---

## 8. Federation: ActivityPub

Cross-organisation work runs through the A2A-bridge pattern (§7).
For workspaces that would federate as peers (think "my org's CHAP
workspace can subscribe to your org's CHAP workspace's events"),
ActivityPub offers a possible mapping, which no profile defines and
no milestone schedules:

| CHAP concept       | ActivityPub concept |
|-------------------|---------------------|
| Workspace         | Actor                |
| Participant       | Actor                |
| `task.create`     | `Create` Activity    |
| `decide.approve`  | `Accept` Activity    |
| `decide.reject`   | `Reject` Activity    |
| Workspace member list | `following` collection |

Federation is not required for anything CHAP does today.

---

## 9. Provisioning: SCIM 2.0

When human Participants are provisioned or deprovisioned through an
external identity-management system,
[SCIM 2.0](https://datatracker.ietf.org/doc/html/rfc7644) user events
could map to `participant.join` and `participant.leave`: a possible
mapping, which no profile defines. `participant.leave` removes only
its caller; removing another member needs `workspace.evict`, which is
not built.

---

## 10. Transport

The wire format, a JSON-RPC 2.0 call with the CHAP fields in
`params`, is the same on every transport. Today:

- **HTTP POST**: required by Core, and what both reference servers
  speak.
- **MCP and A2A**: through the adapter packages (§7.2).
- **WebSocket, HTTP + SSE and message brokers**: specified in
  SPECIFICATION.md §14, built in neither coordinator.

Neither reference coordinator pushes; a client polls `audit.read`.
Milestone 0.5 decides on notifications.

---

## 11. URIs

CHAP Participant URIs use [RFC 3986](https://datatracker.ietf.org/doc/html/rfc3986)
generic syntax, with the prefixes `human:`, `agent:`, `service:`,
`group:` and `workspace:`. The prefixes are not registered.
[`SPECIFICATION.md`](./SPECIFICATION.md) §18 proposes registering each
at Last Call, and milestone 0.4 settles the format of participant
names.

For workspace identifiers that need to be cryptographically
verifiable across the network, [W3C DIDs](https://www.w3.org/TR/did-core/)
can be used as the URI's authority component. This is optional;
typical deployments use plain DNS authorities.

---

## 12. Versioning

Before 1.0 a minor release may break things, and its changelog lists
each break with a migration. From 1.0 the specification follows
[Semantic Versioning 2.0](https://semver.org)
([`ROADMAP.md`, Version numbers](./ROADMAP.md#version-numbers)).

Profiles version independently from Core. A workspace advertises the
profiles it serves with their versions, such as `review/1.0`.

---

## 13. Conformance attestation: in-toto

The conformance harness's `--attest` option writes an
[in-toto Statement](https://github.com/in-toto/attestation) with
predicate type `https://chap.dev/conformance/v1`, naming the endpoint
it tested and each test's result. Standard supply-chain tooling can
sign the Statement and check it later. The self-assessment checklist
is in [`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md).

---

## 14. What CHAP introduces that doesn't exist elsewhere

Stripped of the reused standards, CHAP introduces:

1. **A specific set of human-agent verbs**: `task.create`,
   `task.update`, `task.complete`, `review.request`,
   `decide.approve`, `decide.reject`, `decide.override`,
   `abstain.declare`, `escalate.raise`, `whisper.ask`,
   `whisper.answer`, `handoff.propose`, `handoff.accept`,
   `deliberate.open/comment/vote/close`. None of these exist
   anywhere as standardised methods.
2. **The structured-override shape**: diff + rationale + tags +
   policy_refs, attached to a base artefact, queryable as data.
   This is the single most novel piece.
3. **The mode promotion ladder**: shadow → trial → production as
   a typed property of tasks and workspaces. The coordinator enforces
   the mode ceiling and trial review. A workspace's mode is fixed at
   creation until `workspace.set_mode` is built;
   `control.set_mode_ceiling`, privileged and recorded, sets the
   highest mode a new task may carry.
4. **Typed abstention**: `abstain.declare` as a positive signal
   distinct from rejection or silence.
5. **The whisper primitive**: a deadline-bound interrupt question
   with a defined default-if-lapsed, distinct from review.

These are CHAP's actual contribution to the protocol ecosystem.
The rest is composition.

---

## 15. Summary

| Layer            | CHAP's contribution                            | Source of standards |
|------------------|-----------------------------------------------|---------------------|
| Transport        | None                                          | HTTP; WebSocket, SSE and brokers specified and unbuilt |
| Encoding         | None                                          | JSON, JSON-RPC 2.0, JCS, JSON Patch |
| Identity         | None                                          | OIDC, DPoP, VC, SPIFFE |
| Audit            | The log and its optional hash chain; `audit-scitt` hands statements to SCITT | SCITT (RFC 9943), COSE |
| Cryptography     | None                                          | Ed25519 (RFC 8032), SHA-256 |
| Methods          | **The Core and profile methods**              | CHAP itself |
| Override shape   | **The override-with-rationale primitive**     | CHAP itself |

CHAP is, deliberately, a thin layer of well-chosen verbs on top of
a deep stack of existing standards. If you find yourself
reinventing one of the rows above in your CHAP implementation,
you're doing it wrong.
