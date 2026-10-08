# CHAP Glossary

This glossary covers terms used in the CHAP specification, the architecture
document, and the worked examples. Terms used normatively in the spec carry a
[normative] tag; the rest are informative.

---

## A

**A2A.** [Agent-to-Agent Protocol](https://a2a-protocol.org). An open protocol for
agent communication across organisational boundaries. CHAP composes with A2A
in two directions: outbound via a bridge service participant, and inbound
by exposing a Coordinator *as* an A2A agent with every CHAP method as a
skill on its Agent Card. See
[`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

**Abstention.** A Participant's recorded decision *not* to decide. Used when
the Participant has insufficient information, insufficient authority, or a
conflict of interest. Triggered by `abstain.declare`, which records the
reason and category on the review and moves the task to `abstained`. No
abstention artefact is produced.

**Actor.** [normative] The Participant on whose behalf an envelope is sent,
named in the `from` field. For every method other than `participant.join`,
the actor MUST be a joined member of the named workspace (the error table
calls a breach `unknown_participant`; the reference implementations return
`not_authorised`, -32011). Both coordinators check this for the Core task
methods, the `review/1.0` methods, `workspace.set_profiles` and every
control, deliberation, handoff and whisper method. The `routing/1.0`
methods and `participant.leave` do not check it yet; `workspace.create`,
`participant.join`, `audit.submit_to_scitt` and the reads are exempt by
design. See SPECIFICATION.md S6.3.1.

**Admin (role).** [normative] A workspace role. Both coordinators require it
for `workspace.set_profiles` and for revoking another member's key. The
specification also gives it inviting and evicting Participants and changing
the mode, through methods not yet built. Neither coordinator controls
admission yet: `participant.join` admits any caller in the role it asks for.
Until milestone 0.6, authenticate joins and enforce role policy in front of
the coordinator.

**Anchor.** An external publication of an evidence-chain head, for example
to a transparency log, so that tampering can be shown outside the
Coordinator. `audit.submit_to_scitt` hands statements to a transparency
service the deployment connects; anchoring a chain head itself is specified
and not yet built.

**Artefact.** [normative] A typed payload produced by a Participant inside a
Task (a draft, a decision, an override, a citation set, a structured record).
See §9 of the specification.

**Assignee.** The Participant a Task is assigned to.

**`auth_time`.** OIDC ID-token claim recording when the human authenticated.
CHAP uses `auth_time` to enforce step-up windows for privileged operations.

**Authority (URI portion).** The `@authority` suffix on a Participant URI
(e.g. `human:alice@example.org`). Identifies the issuing identity domain.

---

## B

**Break-glass.** An informative term for admitting an actor outside the
normal membership flow to handle an emergency (for example, a senior
approver stepping in to decide a stalled review). CHAP does not define a
distinct break-glass method. The recommended pattern is a *flagged join*:
the actor joins via `participant.join` carrying a role or flag that marks
the entry as exceptional, then acts as a normal member. Because the join
is itself an audited evidence entry, the exceptional admission is on the
record and no decision is ever attributed to a non-member.

---

## C

**Capability profile.** [normative] A Participant's self-reported set of
abilities, supported task kinds, supported modes, latency, concurrency.
Descriptive, not prescriptive: it informs routing but does not grant
authority.

**Capture fragment.** An ad-hoc evidence entry created via `capture.append`.
Used to attach a note, tag, link, or observation to an active task without
producing a full artefact. Specified; not yet built.

**Checkpoint.** A Coordinator-signed evidence entry asserting the chain
head and length at a point in time. Default interval: every 1000 entries.
Specified; not yet built.

**Citation.** A reference inside an artefact to an external source.
typically an MCP tool invocation or an A2A correlation, with hashes of the
input and output for integrity verification.

**Coordinator.** [normative] The service that mediates a workspace: it
checks each call, applies the rules of the profiles the workspace
advertises, answers the caller and appends to the audit log. It is not a
member of the workspaces it mediates. Exactly one per workspace.

**`cnf.jwk`.** OIDC confirmation method ([RFC 7800]) carrying a JWK that
binds the ID token to a specific public key. CHAP uses this to bind a
human's ephemeral signing key to an OIDC session.

[RFC 7800]: https://www.rfc-editor.org/rfc/rfc7800

---

## D

**Decision rule.** [normative] The predicate that determines whether enough
reviewers have weighed in to terminate a review or a deliberation.
`review/1.0` accepts `any_one_approves`, `all_approve` and `quorum:n`.
`deliberation/1.0` accepts those three and `weighted_vote:threshold` and
`weighted_vote_with_veto:threshold`, set on each `deliberate.open`.

**Delegator.** The Participant that assigned a Task. Recorded in the task
descriptor and the evidence chain.

**Deliberation.** A multi-party thread opened by `deliberate.open`. Carries
comments and votes; closes with a computed outcome under the decision rule
set on its `deliberate.open`.

**DPoP.** [RFC 9449]. OAuth 2.0 Demonstrating Proof of Possession. CHAP
borrows DPoP's pattern (a `cnf.jwk` claim binding token to key) for
human identity.

[RFC 9449]: https://www.rfc-editor.org/rfc/rfc9449

---

## E

**Ed25519.** [RFC 8032]. The Edwards-curve digital signature algorithm
used to sign calls under `security-signed/1.0`.

[RFC 8032]: https://www.rfc-editor.org/rfc/rfc8032

**Envelope.** [normative] A JSON-RPC 2.0 call: `jsonrpc`, `id`, `method` and
`params` (`workspace`, `from`, `to`, `ts`), plus a top-level `sig` under
`security-signed/1.0` ([`core/SPEC.md`](./core/SPEC.md) §2). SPECIFICATION.md
§4 describes another shape, reconciled in milestone 0.4.

**Escalation.** Handing a Task up the chain to a higher-authority
Participant, typically because the current assignee is unable or unwilling
to decide. Triggered by `escalate.raise`.

**Evidence entry.** [normative] One entry on a workspace's log: an accepted
state-changing call, or, from 0.3.0, a member's governed refusal under
`request` with its outcome; reads are never recorded. An entry holds `seq`,
`arrived`, the call, and a `prev_hash` when the chain is on.

---

## F

**`from`.** [normative] The originating Participant of a message. Under
`security-signed/1.0`, the signature MUST verify against a key registered to
this Participant, chosen by the message's timestamp; the Coordinator judges
revocation and expiry by its own clock.

---

## G

**Genesis entry.** The first entry on a workspace's log. With the chain on,
its `prev_hash` is `sha256:` followed by 64 zeros.

**Group.** [normative] A Participant URI prefix (`group:`) naming a set of
Participants. Neither coordinator models group membership: a review,
whisper or handoff addressed to a `group:` URI can be answered by any
workspace member. A deliberation's voter list is matched entry by entry, so
a `group:` entry there does not expand to members.

---

## H

**Handoff.** Transferring an in-progress Task from one Participant to
another (e.g. shift change). Triggered by `handoff.propose` and
`handoff.accept`.

**Hash chain.** [normative] The optional hash-linking of a workspace's log,
switched on by `audit-scitt/1.0` or a coordinator option. `prev_hash` is the
head before an entry; the new head is SHA-256(JCS(record) || prev_hash), the
record being an accepted envelope or, from 0.3.0, `{outcome, request}` of a
refusal. `seq` and `arrived` are not hashed.

**Human.** [normative] A Participant URI prefix (`human:`) for human users.

---

## I

**`id`.** [normative] Any string unique to its sender that identifies a
message ([`core/SPEC.md`](./core/SPEC.md) §2.3); ULIDs are recommended.
Refusing a repeated `id` at acceptance is a deployment-level defence
described in [SECURITY.md](./SECURITY.md#envelope-id-replay); neither
reference implements it, so no error code is allocated for it.

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
encoding used as the signing input under `security-signed/1.0`. The same
encoding feeds the chain hash and artefact digests.

[RFC 8785]: https://www.rfc-editor.org/rfc/rfc8785

**JWK.** [RFC 7517]. JSON Web Key. The format used to publish CHAP signing
keys.

[RFC 7517]: https://www.rfc-editor.org/rfc/rfc7517

**JWKS.** A set of JWKs. A participant lists its keys as `jwks` at
`participant.join`, and `workspace.describe` returns each member's keys.

---

## K

**`kid`.** Key ID. Identifies a specific key within a JWKS. Appears in the
top-level `sig` value, `ed25519:<kid>:<base64>`.

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
agent-to-tool protocol. CHAP composes with MCP in two directions: by
*citing* tool invocations inside its evidence chain (outward), and by
exposing a Coordinator *as* an MCP server with every CHAP method as
a tool (inward). See
[`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md).

**Message.** [normative] A single CHAP envelope. An accepted state-changing
call becomes one evidence entry, and from 0.3.0 so does a member's governed
refusal, under `request` with its outcome; reads are never recorded.

**Method.** [normative] The verb of a CHAP request or notification, of the
form `namespace.verb` (e.g. `task.create`, `decide.approve`). Catalogued
in [`schemas/profiles/chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json).

**Mode.** [normative] The operational regime of a workspace or task:
`shadow`, `trial`, or `production`. See §11 of the specification.

**Mode ceiling.** [normative] The maximum mode a workspace's tasks may
carry. Enforced by the Coordinator on every `task.create` and
`control.supersede`.

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
endpoints. The descriptor schema's `policy_uri` is stored by neither
coordinator, whose descriptors reference only a routing policy, by
`routing_policy_uri`. Neither coordinator controls admission yet:
`participant.join` admits any caller in the role it asks for. Until
milestone 0.6, authenticate joins and enforce role policy in front of the
coordinator.

**Privileged method.** [normative] A method that step-up authentication
guards when enforced: the control methods, `workspace.set_profiles` and the
two key methods.

---

## R

**Recipient.** The `to` field of a message. May be a single Participant
URI or an array. Neither coordinator models group membership: a `group:` or
`workspace:` address on a review or a whisper is a broadcast that any
workspace member satisfies.

**Redaction.** Replacing the content of a prior evidence entry while
preserving its hash and signature. Triggered by `audit.redact`; itself
recorded as an evidence entry. Specified; not yet built.

**Request.** [normative] A CHAP message type expecting a response. Carries
`method` and `params`.

**Response.** [normative] A CHAP message type answering a previous request.
Carries `result` on success or `error` on failure. The `id` matches the
request's `id`.

**Review.** A bounded approval step in which one or more reviewers
evaluate an artefact and produce a decision under the task's decision
rule. Opened by `review.request`; closed by `decide.*` operations.

**Reviewer set.** [normative] The reviewers a review was addressed to: the
`to` set on `review.request`, or, where a required review was opened by
`task.complete`, the human members other than the completer and the
assignee. Under `review/1.0`, only a
member in the reviewer set may act on the review (`decide.*`,
`abstain.declare`); a member outside it is rejected with `-32011`. The
decision rule governs how many of the set must decide; the set governs
who is eligible. See profiles/review.md S3.2.

**Role.** A workspace-local label attached to each Participant entry in
the workspace descriptor, taken from `participant.join` (default
`participant`). The specification reserves `coordinator` and `admin`.
Neither coordinator controls admission yet: `participant.join` admits any
caller in the role it asks for. Until milestone 0.6, authenticate joins and
enforce role policy in front of the coordinator.

**Routing hints.** Optional `routing_hints` object on a Task or Artefact
carrying runtime signals consumed by the `routing/1.0` profile. On a
Task: `criticality`, `deadline`, `max_cost_usd`, `risk_tier`: the
budget. On an Artefact: `confidence`, `model_id`, `cost_consumed_usd`,
`latency_ms`: the measurement. CHAP defines the field shape and records
the values with the call, signed under `security-signed/1.0` and hashed when
the chain is on, but assigns them no semantics; interpretation is the
operator's.

**Routing policy.** A document referenced via the workspace's
`routing_policy_uri` that defines the rules consumed by the
`routing/1.0` profile methods. Opaque to CHAP; the protocol carries
a `policy_id` reference, not the policy itself.

**Route decision.** An artefact of kind `route_decision` produced by
each call to `task.route`, `review.depth`, or `escalate.auto`. Records
the decision type, outcome, policy id, hints consulted, and rationale.
The Coordinator holds it in the workspace and returns it to the caller; the
audit log records the request.

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
output. The descriptor schema's `shadow_observers` is stored by neither
coordinator; delivering shadow output to them is the deployment's job.

**Signature.** [normative] An Ed25519 signature over the JCS form of the
call without `sig`, carried as `sig: ed25519:<kid>:<base64>`.

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
resolved inside a workspace. Has a lifecycle (created → in_progress → … →
completed/cancelled/superseded). See §8.1 of the specification for the
exhaustive transition table.

**Tags (override).** Workspace-defined categorisations attached to an
override artefact (e.g. `tone-adjustment`, `compensation-offered`).
Useful for analysing override patterns over time.

**Trial.** [normative] The middle mode. Output reaches a limited audience
(specified observers or a percentage of traffic) and remains gated for
review.

**`ts`.** [normative] The sender's UTC timestamp, with millisecond
precision. Neither coordinator requires it to increase; the log is ordered
by arrival.

---

## U

**ULID.** [Universally Unique Lexicographically Sortable Identifier](https://github.com/ulid/spec).
26-character Crockford-base32. Recommended for message `id` fields, and
used in the identifiers the coordinators mint for workspaces, tasks,
artefacts, deliberations and handoffs. Log entries are numbered by `seq`.

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
