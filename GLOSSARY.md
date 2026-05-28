# CHAP Glossary

This glossary covers terms used in the CHAP specification, the architecture
document, and the worked examples. Terms used normatively in the spec carry a
[normative] tag; the rest are informative.

---

## A

**A2A.** [Agent-to-Agent Protocol](https://a2a.dev). An open protocol for
agent communication across organisational boundaries. CHAP composes with A2A
via a bridge service participant, see
[`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

**Abstention.** A Participant's recorded decision *not* to decide. Used when
the Participant has insufficient information, insufficient authority, or a
conflict of interest. Triggered by `abstain.declare`; produces an
`abstention` artefact.

**Admin (role).** [normative] A workspace role with the authority to invite
and evict Participants, change mode, and rotate Coordinator responsibilities.

**Anchor.** An external publication of an evidence-chain head (e.g. to a
transparency log) used to provide tamper-evidence beyond the Coordinator's
own signature.

**Artefact.** [normative] A typed payload produced by a Participant inside a
Task (a draft, a decision, an override, a citation set, a structured record).
See §9 of the specification.

**Assignee.** The Participant a Task is assigned to.

**`auth_time`.** OIDC ID-token claim recording when the human authenticated.
CHAP uses `auth_time` to enforce step-up windows for privileged operations.

**Authority (URI portion).** The `@authority` suffix on a Participant URI
(e.g. `human:alice@example.org`). Identifies the issuing identity domain.

---

## C

**Capability profile.** [normative] A Participant's self-reported set of
abilities, supported task kinds, supported modes, latency, concurrency.
Descriptive, not prescriptive: it informs routing but does not grant
authority.

**Capture fragment.** An ad-hoc evidence entry created via `capture.append`.
Used to attach a note, tag, link, or observation to an active task without
producing a full artefact.

**Checkpoint.** A Coordinator-signed evidence entry asserting the chain
head and length at a point in time. Default interval: every 1000 entries.

**Citation.** A reference inside an artefact to an external source.
typically an MCP tool invocation or an A2A correlation, with hashes of the
input and output for integrity verification.

**Coordinator.** [normative] The service participant that mediates a
workspace: routes messages, enforces policy and mode, appends to the
evidence chain. Exactly one per workspace.

**`cnf.jwk`.** OIDC confirmation method ([RFC 7800]) carrying a JWK that
binds the ID token to a specific public key. CHAP uses this to bind a
human's ephemeral signing key to an OIDC session.

[RFC 7800]: https://www.rfc-editor.org/rfc/rfc7800

---

## D

**Decision rule.** [normative] The predicate that determines whether enough
reviewers have weighed in to terminate a review. Standard rules:
`any_one_approves`, `all_approve`, `quorum:n`, `weighted_vote:threshold`,
`weighted_vote_with_veto:threshold`.

**Delegator.** The Participant that assigned a Task. Recorded in the task
descriptor and the evidence chain.

**Deliberation.** A multi-party thread opened by `deliberate.open`. Carries
comments and votes; closes with a computed outcome under the workspace's
decision rule.

**DPoP.** [RFC 9449]. OAuth 2.0 Demonstrating Proof of Possession. CHAP
borrows DPoP's pattern (a `cnf.jwk` claim binding token to key) for
human identity.

[RFC 9449]: https://www.rfc-editor.org/rfc/rfc9449

---

## E

**Ed25519.** [RFC 8032]. The Edwards-curve digital signature algorithm
used to sign every CHAP message.

[RFC 8032]: https://www.rfc-editor.org/rfc/rfc8032

**Envelope.** [normative] The JSON object that wraps every CHAP message:
`chap`, `id`, `ts`, `workspace`, `from`, `to`, `type`, `method|params|result|error`,
`evidence`. See §4 of the specification.

**Escalation.** Handing a Task up the chain to a higher-authority
Participant, typically because the current assignee is unable or unwilling
to decide. Triggered by `escalate.raise`.

**Evidence entry.** [normative] A signed, hash-linked record of one accepted
CHAP message in a workspace's evidence log.

---

## F

**`from`.** [normative] The originating Participant of a message. The
signature MUST be verifiable against this Participant's public key as of
the message's timestamp.

---

## G

**Genesis entry.** The first evidence entry in a workspace's chain.
Produced by `workspace.create` and signed by the creator and the
Coordinator. Its `prev_hash` is the all-zeros hash.

**Group.** [normative] A Participant URI prefix (`group:`) addressing a
named set of Participants. The Coordinator expands group addresses at
dispatch time per the workspace's group-membership table.

---

## H

**Handoff.** Transferring an in-progress Task from one Participant to
another (e.g. shift change). Triggered by `handoff.propose` and
`handoff.accept`.

**Hash chain.** [normative] The per-workspace append-only log where each
entry's `prev_hash` is the SHA-256 of the previous entry's canonical
envelope plus its signature.

**Human.** [normative] A Participant URI prefix (`human:`) for human users.

---

## I

**`id`.** [normative] The ULID identifying a message. Globally unique;
re-use is rejected with error `-32701`.

**`instance_id`.** [normative] Optional artefact-descriptor field
identifying the specific version of an artefact. When present, MUST
equal the artefact's `content_hash` or be deterministically derived
from it. Lets consumers detect byte-identical revisions across the
chain. See §9.2.1.

**`intent_preserved`.** [normative] Optional boolean on an override
or supersession artefact. `true` indicates the new artefact refines
the expression of the same underlying intent (same decision, better
delivery); `false` indicates a different decision substituted for
the original. Informational; CHAP does not constrain semantics.
See §9.4.

---

## J

**JCS.** [RFC 8785]. JSON Canonicalization Scheme. The deterministic JSON
encoding used as the signing input for every CHAP message.

[RFC 8785]: https://www.rfc-editor.org/rfc/rfc8785

**JWK.** [RFC 7517]. JSON Web Key. The format used to publish CHAP signing
keys.

[RFC 7517]: https://www.rfc-editor.org/rfc/rfc7517

**JWKS.** A set of JWKs, typically published at a `jwks_uri` listed in a
Participant's descriptor.

---

## K

**`kid`.** Key ID. Identifies a specific key within a JWKS. May appear as
a hint in the `evidence.sig` value.

---

## L

**`logical_id`.** [normative] Optional artefact-descriptor field
identifying the durable thing the artefact is about. Two artefacts
that share a `logical_id` are two versions of the same underlying
item. Producers SHOULD assign on first creation and reuse on every
revision, override, or supersession. CHAP itself reads only `id`;
`logical_id` is for higher-layer version-graph projection. See
§9.2.1.

---

## M

**MCP.** [Model Context Protocol](https://modelcontextprotocol.io). The
agent-to-tool protocol. CHAP composes with MCP by citing tool invocations
inside its evidence chain, see
[`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md).

**Message.** [normative] A single CHAP envelope. Becomes exactly one
evidence entry.

**Method.** [normative] The verb of a CHAP request or notification, of the
form `namespace.verb` (e.g. `task.assign`, `decide.approve`). Catalogued
in [`schemas/chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json).

**Mode.** [normative] The operational regime of a workspace or task:
`shadow`, `trial`, or `production`. See §11 of the specification.

**Mode ceiling.** [normative] The maximum mode a workspace's tasks may
carry. Enforced by the Coordinator on every `task.assign`.

---

## N

**Notification.** [normative] A CHAP message type that expects no response.
Used for status updates, progress, and pub-sub events.

---

## O

**OIDC.** OpenID Connect. The identity layer on top of OAuth 2.0. CHAP
uses OIDC ID tokens (with `cnf.jwk` binding) to authenticate humans.

**Override.** [normative] An artefact recording a human's modification of
an agent's output, with a JSON Patch diff, rationale, and tags. Triggered
by `decide.override`.

---

## P

**Participant.** [normative] Any entity that can send or receive CHAP
messages, human, agent, service, group, or workspace. See §7 of the
specification.

**Participant URI.** [normative] A URI identifying a Participant. Schemes:
`human:`, `agent:`, `service:`, `group:`, `workspace:`.

**Policy.** [normative] The workspace document mapping roles to allowed
methods, defining mode-promotion rules, retention, and permitted external
endpoints. Referenced from the workspace descriptor by URI.

**Privileged method.** [normative] A method requiring step-up
authentication. Marked `privileged: true` in the method catalogue.

---

## R

**Recipient.** The `to` field of a message. May be a single Participant
URI or an array; group and workspace URIs are expanded at dispatch.

**Redaction.** Replacing the content of a prior evidence entry while
preserving its hash and signature. Triggered by `audit.redact`; itself
recorded as an evidence entry.

**Request.** [normative] A CHAP message type expecting a response. Carries
`method` and `params`.

**Response.** [normative] A CHAP message type answering a previous request.
Carries `result` on success or `error` on failure. The `id` matches the
request's `id`.

**Review.** A bounded approval step in which one or more reviewers
evaluate an artefact and produce a decision under the task's decision
rule. Opened by `review.request`; closed by `decide.*` operations.

**Role.** A workspace-local label attached to each Participant entry in
the workspace descriptor. Determines authority via the workspace policy's
method-role matrix. Two roles are reserved: `coordinator` and `admin`.

**Routing hints.** Optional `routing_hints` object on a Task or Artefact
carrying runtime signals consumed by the `routing/1.0` profile. On a
Task: `criticality`, `deadline`, `max_cost_usd`, `risk_tier`: the
budget. On an Artefact: `confidence`, `model_id`, `cost_consumed_usd`,
`latency_ms`: the measurement. CHAP defines the field shape and signs
the values into the evidence envelope but assigns them no semantics;
interpretation is the operator's.

**Routing policy.** A document referenced via the workspace's
`routing_policy_uri` that defines the rules consumed by the
`routing/1.0` profile methods. Opaque to CHAP; the protocol carries
a `policy_id` reference, not the policy itself.

**Route decision.** An artefact of kind `route_decision` produced by
each call to `task.route`, `review.depth`, or `escalate.auto`. Records
the decision type, outcome, policy id, hints consulted, and rationale.
Provides deterministic auditability of routing logic.

---

## S

**Scope.** [normative] A method name a Participant has declared it is
willing to receive. Distinct from authority (which is granted by policy).

**Service.** [normative] A Participant URI prefix (`service:`) for
non-agent, non-human components (Coordinators, bridges, evidence
exporters).

**Shadow.** [normative] The lowest mode. Output is produced but does not
reach external effects. Used for evaluation and pre-deployment review.

**Shadow observer.** A Participant who receives copies of shadow-mode
output. Listed in the workspace descriptor.

**Signature.** [normative] The Ed25519 signature over the JCS
canonicalisation of the envelope minus `evidence.sig`. Carried in
`evidence.sig`.

**SPIFFE.** [Secure Production Identity Framework for Everyone](https://spiffe.io).
Recommended for agent and service workload identities.

**Step-up authentication.** [normative] A re-authentication of the human
Participant within a configurable recency window before a privileged
operation. Default window: 5 minutes.

**Supersede.** Replace a task with another (terminal). The superseded
task remains in the chain, linked to its successor.

---

## T

**Task.** [normative] A unit of work proposed, accepted, performed, and
resolved inside a workspace. Has a lifecycle (created → assigned → … →
completed/cancelled/superseded). See §8 of the specification.

**Tags (override).** Workspace-defined categorisations attached to an
override artefact (e.g. `tone-adjustment`, `compensation-offered`).
Useful for analysing override patterns over time.

**Trial.** [normative] The middle mode. Output reaches a limited audience
(specified observers or a percentage of traffic) and remains gated for
review.

**`ts`.** [normative] The UTC timestamp on a message, with millisecond
precision. Must be strictly monotonic per `from`.

---

## U

**ULID.** [Universally Unique Lexicographically Sortable Identifier](https://github.com/ulid/spec).
26-character Crockford-base32. Used for all CHAP `id` fields and most
resource identifiers (tasks, artefacts, evidence entries).

---

## V

**Verifier.** Any party that re-checks a workspace's evidence chain.
Typically an auditor, a regulator, or a downstream learning system.

---

## W

**Whisper.** A short, deadline-bound, interrupt-style question sent
mid-task, typically from an agent to a human, asking for a quick
disambiguation. Triggered by `whisper.ask`; answered by
`whisper.answer`. May carry a `default_if_lapsed` value.

**Workspace.** [normative] A named, addressable collaboration context
with a membership list, a policy, a mode, and an append-only evidence
log. The unit of collaboration in CHAP. See §6 of the specification.
