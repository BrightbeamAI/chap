# Changelog

All notable changes to the Collaborative Human-Agent Protocol (CHAP) will be recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the protocol
adheres to [Semantic Versioning 2.0](https://semver.org).

Profiles version independently from Core. A profile version `<name>/<major>.<minor>` is
incremented under the same rules.

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

The protocol surface and schemas are stable enough for adoption. The largest
outstanding work item before 1.0 is a second interoperable implementation, which
unlocks the Full conformance level. Profile surfaces may continue to evolve
faster than Core under the CEP process described in
[`GOVERNANCE.md`](./GOVERNANCE.md).

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
