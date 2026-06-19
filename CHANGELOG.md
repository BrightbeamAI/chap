# Changelog

All notable changes to the Collaborative Human-Agent Protocol (CHAP) will be recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the protocol
adheres to [Semantic Versioning 2.0](https://semver.org).

Profiles version independently from Core. A profile version `<name>/<major>.<minor>` is
incremented under the same rules.

---

## 0.2.1: Python reference implementation

A second reference implementation, in Python, lands as a backward-compatible
addition. No protocol changes; no wire-format changes; no spec changes that
existing 0.2 implementations need to account for.

### Added

- **`packages/coordinator-py/`.** A Python package (`chap-coordinator`)
  providing the Coordinator as a transport-agnostic library. Covers Core (9
  methods) plus every profile: `review/1.0`, `whisper/1.0`,
  `deliberation/1.0`, `handoff/1.0`, `control/1.0`, `routing/1.0`,
  `modes/1.0`, `security-signed/1.0`, `audit-scitt/1.0`,
  `identity-oidc/1.0`, `identity-vc/1.0`. **39 method handlers in total**,
  matching the spec inventory exactly.
- **`reference/python/`.** An HTTP server, a demo client mirroring the
  TypeScript reference, and an `analyze_overrides.py` analytics tool. The
  server speaks the same JSON-RPC 2.0 wire format as the TypeScript
  reference and passes the existing conformance harness.
- **63 tests** covering Core, every profile, the cryptographic test
  vectors, signed-envelope verification, OIDC and VC binding, and an
  end-to-end composition test that exercises every method handler in one
  workspace sequence.

### Spec fidelity notes

The Python implementation was reviewed against every profile spec under
`profiles/` and aligned with the documented field names, error codes, and
response shapes. Specifically:

- **whisper/1.0:** uses `answer_option` (not `answer`); distinguishes
  `WHISPER_ALREADY_ANSWERED` (-32020), `WHISPER_LAPSED` (-32021), and
  `WHISPER_OPTION_NOT_IN_SET` (-32022); exposes `check_whisper_lapses()`
  for the deadline-emit-notify path.
- **deliberation/1.0:** uses `vote` and `comment` (not `choice`/`rationale`);
  emits the flat-outcome response shape; rejects re-votes per
  `DELIB_ALREADY_VOTED` (-32031).
- **handoff/1.0:** carries multiple `tasks` per handoff, accepts a
  single recipient `to` (URI or group), supports first-accept-wins, and
  validates the proposer-owns-the-tasks precondition.
- **control/1.0:** `scope` parameter on pause/resume (task/participant/
  workspace), snapshot returns an artefact id, supersede creates the
  successor task from a `successor_task` object, rollback uses
  `to_snapshot_artefact_id` and `what_to_restore`.
- **security-signed/1.0:** top-level `sig` field (not in params), key
  records carry `valid_from`/`valid_until` so historical envelopes
  verify across rotation, `participant.revoke_key` added.
- **routing/1.0:** every decision produces a `route_decision` artefact;
  `task.route` updates the task assignee; `review.depth` returns a
  `sampling_probability` when applicable.
- **identity-oidc/1.0** and **identity-vc/1.0:** verifier hooks pin the
  `cnf.jwk` (or VP holder key) as the participant's signing key.
- **audit-scitt/1.0:** statements are built per the COSE_Sign1 shape and
  passed to a deployment-supplied `scitt_submitter`; local chain
  linkage is retained as a supplementary integrity check.

### Notes

- The Python package has zero required runtime dependencies for Core. The
  `security-signed/1.0` profile requires `cryptography>=42`, installed via
  `pip install "chap-coordinator[crypto]"`.
- The Python implementation now closes the second-interoperable-
  implementation prerequisite. The Full conformance level becomes
  claimable once the conformance harness has been run cross-language at
  scale (TS client against Python server, and Python client against TS
  server).

---

## 0.2: First public release

The first public release of CHAP. This is a working draft suitable for review,
experimentation, and early production pilots, not yet a stable 1.0 standard.

What this release contains:

- **Core.** A JSON-RPC 2.0 envelope and seven methods (`workspace.describe`,
  `participant.join`, `participant.leave`, `task.create`, `task.update`,
  `task.complete`, `audit.read`). A task lifecycle, a participant model, and an
  append-only evidence log.
- **Eleven profiles.** `review`, `modes`, `routing`, `whisper`, `deliberation`,
  `handoff`, `control`, `identity-oidc`, `identity-vc`, `security-signed`,
  `audit-scitt`. Each independent, each composable.
- **One reference implementation in TypeScript.** Core, Core+Review, a coordinator
  package, a CLI, an override-analytics tool, and a two-participant playground.
- **A conformance harness.** 21 test vectors covering wire format, all seven Core
  methods, and the six Review methods. Two conformance levels claimable today
  (Minimal, Recommended); Full waits on a second interoperable implementation.
- **Twelve worked scenarios** in [`IN_PRACTICE.md`](./IN_PRACTICE.md), spanning a
  solo developer through GMP-regulated manufacturing.
- **Full documentation.** [Specification](./SPECIFICATION.md), [Handbook](./HANDBOOK.md),
  [Architecture](./ARCHITECTURE.md), [Security](./SECURITY.md),
  [FAQ](./FAQ.md), [Glossary](./GLOSSARY.md), and a relationship mapping to
  [other standards](./RELATIONSHIP-TO-OTHER-STANDARDS.md).

### What's next

The protocol surface and schemas are stable enough for adoption. With the
Python reference now in place (see 0.2.1 above), the remaining work toward
1.0 is cross-language interop testing at scale and review of the profile
schemas for any breaking adjustments before the API surface freezes.
Profile surfaces may continue to evolve faster than Core under the CEP
process described in [`GOVERNANCE.md`](./GOVERNANCE.md).

---

## Versioning policy

- **MAJOR** (`X.0`): wire-breaking changes; old clients cannot talk to new
  servers. Migration windows of at least one calendar year between MAJOR versions.
- **MINOR** (`X.Y`): additive only. New methods, new optional fields, new error
  codes. Old clients keep working.
- **PATCH** (`X.Y.Z`): editorial fixes; no semantic change.

Profiles version independently from Core. A workspace declares the specific Core
version and the specific profile versions it implements via
`workspace.describe`'s `profiles` field.
