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
| Audit / transparency log     | **draft-ietf-scitt-architecture**                | Reuses via `audit-scitt` profile. |
| Audit signature format       | **COSE (RFC 9052) + SCITT receipts**             | Reuses via `audit-scitt` profile. |
| Tool calls inside artefacts  | **MCP**                                          | Composed (cited). |
| Cross-org peer delegation    | **A2A**                                          | Composed (bridge participant). |
| Federation between workspaces | **ActivityPub** (Actors, Inbox/Outbox)          | Optional binding (`federation-activitypub` profile, draft). |
| Participant lifecycle (provision/deprovision) | **SCIM 2.0**                  | Optional binding for human provisioning. |
| Transport                    | **WebSocket · HTTP+SSE · MQTT · NATS · Kafka**   | Transport-agnostic; bind to whichever. |
| URIs                         | **RFC 3986**                                     | Plain URIs; CHAP defines the scheme grammar. |
| Versioning                   | **Semantic Versioning 2.0**                      | Spec versions follow semver. |
| Conformance attestations     | **in-toto attestation framework**                | Conformance docs published as in-toto-compatible JSON. |

The only things CHAP introduces that don't exist elsewhere are
**the methods themselves** (`task.create`, `review.request`,
`decide.override`, `abstain.declare`, `whisper.ask`, etc.) and the
**override-with-rationale shape** that turns human edits into
structured learning signals. Everything else is plumbing.

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

CHAP adds a small fixed set of fields (`chap`, `workspace`, `from`, `to`,
`ts`, `type`) that don't conflict with JSON-RPC and are namespaced
by their position in the envelope.

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
This is required only when the `security-signed` or `audit-scitt`
profiles are in use; Core has no canonicalisation requirement.

JCS was chosen because:

1. It's an IETF standard.
2. It produces deterministic bytes from any conformant JSON parser.
3. It has reference implementations in every common language.
4. SCITT also uses it, so CHAP's `audit-scitt` profile composes naturally.

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
The Coordinator verifies the VC's issuer signature and stores the
relevant claims in the participant descriptor.

This is strictly more expressive than OIDC `cnf.jwk` but also more
complex; pick OIDC for typical SaaS deployments and VC when richer
claims actually matter.

### 5.3 SPIFFE for services

Agents and services in a service mesh authenticate with [SPIFFE](https://spiffe.io)
SVIDs. The CHAP signing key is bound to the SPIFFE ID via the mesh's
workload identity infrastructure.

---

## 6. Audit / transparency log: SCITT

CHAP's audit defers to a mature, IETF-tracked standard rather than
defining its own transparency primitive.

The earlier draft defined its own append-only hash-chained log with
custom canonicalisation and signature rules. That was reinvention.

The [IETF SCITT (Supply Chain Integrity, Transparency and Trust)](https://datatracker.ietf.org/wg/scitt/about/)
working group is producing exactly that primitive as a standard.
SCITT is built on COSE ([RFC 9052](https://datatracker.ietf.org/doc/html/rfc9052))
and produces signed receipts that any party can verify offline.

CHAP's `audit-scitt` profile says: **the workspace's evidence chain
is a SCITT transparency log.** Specifically:

- Each CHAP message becomes a SCITT signed statement.
- The Coordinator (or a third party) operates the SCITT Transparency
  Service and issues receipts.
- Auditors verify receipts against the transparency service's signed
  log root; CHAP doesn't define a parallel verification path.

For Core-only deployments that don't need cryptographic audit, the
log can be a plain database table, no SCITT involvement, no
crypto. The profile is opt-in.

See [`profiles/audit-scitt.md`](./profiles/audit-scitt.md).

---

## 7. Composition with MCP and A2A

CHAP doesn't re-implement either MCP or A2A. Composition is by
**citation**, not encapsulation:

- An MCP tool call called by an agent during CHAP work becomes a
  `citation` of kind `mcp_tool_invocation` inside the agent's CHAP
  artefact, with `input_hash` and `output_hash` providing the
  hash boundary. See [`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md).
- An A2A peer is represented inside a CHAP workspace by a
  `service:bridge…` Participant. Cross-org work goes via that
  Participant; the A2A traffic does not cross the CHAP wire. See
  [`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

The MCP and A2A protocols evolve on their own timelines; CHAP's
citation pattern is forward-compatible with their major versions.

---

## 8. Federation: ActivityPub (draft)

Cross-organisation workspace-to-workspace federation is currently
specified via the A2A-bridge pattern (§7). For deployments where
the workspaces themselves are first-class federation peers (think
"my org's CHAP workspace can subscribe to your org's CHAP workspace's
events"), the planned `federation-activitypub` profile (post-v0.2)
maps:

| CHAP concept       | ActivityPub concept |
|-------------------|---------------------|
| Workspace         | Actor                |
| Participant       | Actor                |
| `task.create`     | `Create` Activity    |
| `decide.approve`  | `Accept` Activity    |
| `decide.reject`   | `Reject` Activity    |
| Workspace member list | `following` collection |

This is an optional profile. Federation is not required and is not
on the current critical path.

---

## 9. Provisioning: SCIM 2.0

When human Participants are provisioned/deprovisioned from a
workspace via an external identity-management system, the
`provisioning-scim` profile (optional) defines the mapping from
[SCIM 2.0](https://datatracker.ietf.org/doc/html/rfc7644) user
events to CHAP `participant.join` and `participant.leave`.

Again, optional. A simple deployment can manage membership
manually via direct workspace admin operations.

---

## 10. Transport

CHAP is transport-agnostic. The recommended bindings are:

- **WebSocket**: for interactive clients (humans, GUIs).
- **HTTP + SSE**: for firewall-friendly fallback.
- **MQTT / NATS / Kafka**: for high-throughput server-to-server flows.

Each binding is a thin adapter; the wire format (JSON-RPC 2.0 +
CHAP fields) is identical across them. Existing standards-based
deployments (mTLS-secured WebSockets behind a service mesh; SSE
behind a typical load balancer; Kafka with SASL+SCRAM) require no
CHAP-specific transport plumbing.

---

## 11. URIs

CHAP Participant URIs use [RFC 3986](https://datatracker.ietf.org/doc/html/rfc3986)
generic syntax. The CHAP-specific schemes (`human:`, `agent:`,
`service:`, `group:`, `workspace:`) are registered under a single
parent scheme `chap:` for IANA registration purposes; see
[`SPECIFICATION.md`](./SPECIFICATION.md) §18.

For workspace identifiers that need to be cryptographically
verifiable across the network, [W3C DIDs](https://www.w3.org/TR/did-core/)
can be used as the URI's authority component. This is optional;
typical deployments use plain DNS authorities.

---

## 12. Versioning

Spec versions follow [Semantic Versioning 2.0](https://semver.org).
Wire-format breaking changes are major-version events. Adding a
method is a minor-version event. Adding an optional field to an
existing method is a patch.

Profiles version independently from Core. A workspace declares the
specific Core version and the specific profile versions it
implements.

---

## 13. Conformance attestation: in-toto

When an implementation attests to conformance with a CHAP level
(see [`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md)),
the attestation is published as an
[in-toto attestation](https://github.com/in-toto/attestation)
with subject `chap-implementation:<name>:<version>` and predicate
`chap.dev/conformance/v1`. This lets standard supply-chain tooling
discover and verify CHAP conformance claims.

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
   a typed property of tasks and workspaces, enforced at the
   protocol layer.
4. **Typed abstention**: `abstain.declare` as a positive signal
   distinct from rejection or silence.
5. **The whisper primitive**: a deadline-bound interrupt question
   with a defined default-if-lapsed, distinct from review.

These are CHAP's actual contribution to the protocol ecosystem.
The rest is composition.

---

## 15. The honest summary

| Layer            | CHAP's contribution                            | Source of standards |
|------------------|-----------------------------------------------|---------------------|
| Transport        | None                                          | TCP / WebSocket / HTTP / Kafka |
| Encoding         | None                                          | JSON, JSON-RPC 2.0, JCS, JSON Patch |
| Identity         | None                                          | OIDC, DPoP, VC, SPIFFE |
| Audit            | None (in `audit-scitt`)                       | SCITT, COSE |
| Cryptography     | None                                          | Ed25519 (RFC 8032), SHA-256 |
| Federation       | None (when using ActivityPub binding)         | ActivityPub |
| Provisioning     | None (when using SCIM binding)                | SCIM 2.0 |
| Methods          | **The 7 Core + 20+ profile methods**          | CHAP itself |
| Override shape   | **The override-with-rationale primitive**     | CHAP itself |

CHAP is, deliberately, a thin layer of well-chosen verbs on top of
a deep stack of existing standards. If you find yourself
reinventing one of the rows above in your CHAP implementation,
you're doing it wrong.
