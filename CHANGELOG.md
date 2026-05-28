# Release Notes

All notable changes to the Collaborative Human-Agent Protocol are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the protocol adheres to [Semantic Versioning 2.0](https://semver.org).

Profiles version independently from Core. A profile version
`<name>/<major>.<minor>` is incremented per the same rules.

---

## 0.2.1 — pre-release editorial pass

Editorial revision of 0.2 after an external two-lens review. No
breaking changes; additions are backward-compatible and optional.

### Added

- **Artefact identity fields (§9.2.1).** Optional `logical_id` and
  `instance_id` on the artefact descriptor. `logical_id` is the
  durable handle for the *thing the artefact is about* across
  revisions; `instance_id` is a stable handle for a specific version
  (and, when present, MUST be tied to `content_hash`). CHAP itself
  reads only `id`; the new fields enable version-graph projection in
  consumer tools and analytics.
- **`intent_preserved` on overrides and supersessions (§9.4).**
  Optional boolean distinguishing "the human refined the expression
  of the same decision" from "the human substituted a different
  decision". Informational; CHAP does not constrain semantics. Same
  convention applies to `control.supersede`.
- **Threat-model subsection (§15.4).** New normative subsection
  inside Security considerations covering replay, downgrade,
  capability confusion across profiles, key rotation, evidence-chain
  forking under partition, compromised Coordinator, and identity
  confusion — each named with the protocol-level countermeasure and
  the deployment-level mitigation where the protocol cannot defend
  alone. An explicit out-of-scope list closes the section.
- **Maturity statement** at the top of SPECIFICATION.md and in the
  README, naming 0.2 as a public Draft with one reference
  implementation and noting that the two-interoperable-implementations
  bar typical of standards-track promotion has not yet been met.
- **Expanded Non-goals (§1.3).** Five additional bullets clarifying
  what CHAP deliberately leaves to deployments and profiles: claim
  taxonomies, temporal models beyond `produced_at`, confidence
  calibration, regulatory-sufficiency determinations, and semantic
  relations between artefacts beyond `based_on`/supersession.

### Changed

- **§17 Conformance reworked.** *Full* is now marked **planned**, not
  claimable in this revision: it requires a second interoperable
  implementation and an exhaustive interop test suite that the
  current draft does not yet have. Implementations may continue to
  attest at Minimal or Recommended. Self-attestation rules tightened
  to require honesty about implemented-vs-declared methods.
- **`review/1.0` profile.** `decide.override` example now includes
  `logical_id` and `intent_preserved`, with prose explaining when
  each SHOULD be set.
- **`chap-task.schema.json`.** Artefact gains optional `logical_id`
  (`lgl_…` pattern) and `instance_id`. OverrideContent gains optional
  `logical_id` and `intent_preserved`.
- **GLOSSARY.** Added `logical_id`, `instance_id`, `intent_preserved`
  as normative entries.
- **Reference implementations updated.** The `@chap/coordinator`
  package and the `core-plus-review` server now persist the new
  optional fields on override artefacts when clients emit them
  (pass-through; no synthesis). The `core-plus-review` demo client
  emits both fields; the playground UI emits both fields with
  `intent_preserved: true` as the default for tone-adjustment
  overrides. The `analyze-overrides` analytics tool now reports a
  refined-vs-replaced breakdown when clients supply
  `intent_preserved`. Smoke tests cover both new-client and
  legacy-client paths.

### Removed

- Nothing. All changes are additive.

### Migration notes

No code changes are required for existing 0.2 implementations. The
new fields are optional; existing artefacts continue to validate.
Implementations that wish to surface version-graph semantics or
intent-distinction analytics SHOULD start emitting `logical_id` on
first artefact creation; legacy artefacts can be retrofitted by an
index that hashes `(workspace, task, kind, …)` to a synthetic
`logical_id`.

---

## 0.2 — current

The Collaborative Human-Agent Protocol at full surface: Core, eleven profiles,
schemas, reference implementations, examples, conformance suite,
and standards bindings.

### Core (`core/1.0`)

- JSON-RPC 2.0 envelope with CHAP-specific fields (`workspace`,
  `from`, `to`, `ts`) inside `params`.
- Seven methods: `workspace.describe`, `participant.join`,
  `participant.leave`, `task.create`, `task.update`,
  `task.complete`, `audit.read`.
- Three task states: `created`, `in_progress`, `completed`
  (with `declined` as terminal alternative).
- Per-workspace audit log; in-memory or durable, no cryptographic
  chaining required.
- HTTP POST transport binding required; WebSocket, HTTP+SSE,
  Kafka/NATS optional.
- TLS required for production; bearer-token authentication.
- Standard JSON-RPC 2.0 error codes (-32700 through -32603).

### Profiles

| Profile                         | Scope                                                   |
|---------------------------------|---------------------------------------------------------|
| [`security-signed/1.0`](./profiles/security-signed.md) | Ed25519 message signatures over RFC 8785 JCS bytes; key registration, rotation, revocation. |
| [`audit-scitt/1.0`](./profiles/audit-scitt.md)         | IETF SCITT transparency-service binding; signed statements, receipts. |
| [`identity-oidc/1.0`](./profiles/identity-oidc.md)     | OIDC ID-token binding with `cnf.jwk` (RFC 7800) or DPoP (RFC 9449); step-up auth. |
| [`identity-vc/1.0`](./profiles/identity-vc.md)         | W3C Verifiable Credentials 2.0 binding; selective disclosure; StatusList revocation. |
| [`review/1.0`](./profiles/review.md)                   | `review.request`, `decide.approve`, `decide.reject`, `decide.override` (RFC 6902 JSON Patch + rationale + tags + policy_refs), `abstain.declare`, `escalate.raise`. |
| [`whisper/1.0`](./profiles/whisper.md)                 | `whisper.ask` / `whisper.answer` with deadline-bound default-if-lapsed. |
| [`deliberation/1.0`](./profiles/deliberation.md)       | `deliberate.open/comment/vote/close` with quorum, weighted, and veto rules. |
| [`modes/1.0`](./profiles/modes.md)                     | Shadow → trial → production task mode ladder with workspace-level mode ceiling. |
| [`handoff/1.0`](./profiles/handoff.md)                 | `handoff.propose/accept/decline`; group-routed handoffs with first-accept wins. |
| [`routing/1.0`](./profiles/routing.md)                 | `task.route`, `review.depth`, `escalate.auto` driven by `routing_hints` on tasks/artefacts. Optional, composes with `review/1.0` and `modes/1.0`. |
| [`control/1.0`](./profiles/control.md)                 | `control.pause/resume/cancel/supersede/snapshot/rollback`; append-only rollback semantics. |

### Integration patterns

- [CHAP with MCP](./integrations/CHAP-with-MCP.md): cite MCP tool
  calls inside CHAP artefacts with input/output hashes.
- [CHAP with A2A](./integrations/CHAP-with-A2A.md): bridge external
  A2A peers as `service:bridge…` participants.
- [CHAP with OIDC / OAuth 2.0](./integrations/CHAP-with-OIDC-OAuth2.md):
  deployment patterns for the identity binding.
- [Deployment patterns](./integrations/CHAP-deployment-patterns.md):
  Coordinator topology, transport selection, audit-store sizing.

### Schemas

- Core schemas (envelope, participant, task, workspace) under
  [`schemas/core/`](./schemas/core/).
- Profile schemas (evidence, methods) under [`schemas/profiles/`](./schemas/profiles/).

### Reference implementations

- [`reference/core/`](./reference/core/): minimal Core in ~400 lines
  of TypeScript. Implements all 7 methods, in-memory state, plain
  HTTP + JSON-RPC. Weekend-buildable.
- [`reference/core-plus-review/`](./reference/core-plus-review/):
  Core + the full Review profile (13 methods total). Includes an
  RFC 6902 JSON Patch implementation and an `analyze-overrides.ts`
  tool that demonstrates the structured-override learning-data
  dividend.

### Demo

- [`demo/index.html`](./demo/index.html): a single-file interactive
  HTML walkthrough. Tells CHAP's story in five minutes — problem
  framing, the track-changes analogy, the protocol stack, an
  interactive workspace simulation, the override-as-data dividend,
  and the profile picker. Self-contained, works offline.

### Conformance

- [Conformance checklist](./conformance/conformance-checklist.md):
  per-profile attestation template.
- [Test vectors](./conformance/test-vectors.md): canonical inputs
  and outputs for wire-format, JCS, Ed25519, and method-level
  conformance.
- [`conformance/harness/`](./conformance/harness/): runnable test
  harness that validates any CHAP endpoint against the canonical
  vectors and produces an [in-toto attestation](https://github.com/in-toto/attestation)
  on success. 21 tests covering wire format, all 7 Core methods,
  and the 6 Review methods.

### Governance

- CEP (CHAP Enhancement Proposal) process documented in
  [`GOVERNANCE.md`](./GOVERNANCE.md).
- License: Apache 2.0 (code) + CC-BY 4.0 (specification).
- Independent profile versioning; backward-compatible additions are
  minor-version events, breaking changes are major-version events.

### Documentation

- [README](./README.md): protocol overview and reading paths.
- [Handbook](./HANDBOOK.md): operator's manual covering workspace
  design, profile selection, rollout, override capture, identity,
  audit, deployment, monitoring, incident response, patterns, and
  anti-patterns.
- [FAQ](./FAQ.md): preempts common questions.
- [Architecture](./ARCHITECTURE.md): design rationale.
- [Security](./SECURITY.md): threat model and disclosure policy.
- [Glossary](./GLOSSARY.md): term reference.
- [Relationship to other standards](./RELATIONSHIP-TO-OTHER-STANDARDS.md):
  explicit mapping to OIDC, VC, SCITT, JCS, JSON Patch, JSON-RPC,
  ActivityPub, SCIM, in-toto, SPIFFE.

### Standards reused

CHAP reuses, rather than reinvents, the following standards:

| Layer       | Standard                                                            |
|-------------|---------------------------------------------------------------------|
| Encoding    | JSON-RPC 2.0; RFC 8785 (JCS); RFC 6902 (JSON Patch)                 |
| Identity    | OIDC; RFC 7800 (`cnf.jwk`); RFC 9449 (DPoP); W3C VC 2.0; W3C DIDs; SPIFFE |
| Audit       | IETF SCITT; RFC 9052 (COSE); Ed25519 (RFC 8032)                     |
| Federation  | ActivityPub (optional binding)                                       |
| Provisioning | SCIM 2.0 (optional binding)                                         |
| Attestation | in-toto                                                              |

The only protocol-level inventions are the methods themselves and
the override-with-rationale primitive (diff + rationale + tags +
policy_refs).

---

## Versioning policy

- **MAJOR** (`X.0`): wire-breaking changes; old clients cannot
  talk to new servers. A documented migration window of at least
  one calendar year between MAJOR versions.
- **MINOR** (`X.Y`): additive only. New methods, new optional
  fields, new error codes. Old clients keep working.
- **PATCH** (`X.Y.Z`): editorial fixes; no semantic change.

Profiles version independently from Core. A workspace declares the
specific Core version and the specific profile versions it
implements via `workspace.describe`'s `profiles` field.

---

## Future direction

Items under active consideration for future versions, tracked as
CEPs in the issue tracker:

- `federation-activitypub`: workspace-to-workspace federation via
  ActivityPub Actor/Inbox model.
- `provisioning-scim`: SCIM 2.0 binding for human-participant
  lifecycle.
- Additional signature algorithms in `security-signed` for
  compliance with regional cryptographic regulations.
- `policy-cedar`: optional Cedar-language workspace policy binding.

These are not part of the current release and have no committed
timeline; they appear here so the direction is visible.
