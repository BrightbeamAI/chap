# Changelog

All notable changes to the Collaborative Human-Agent Protocol (CHAP) will be recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the protocol
adheres to [Semantic Versioning 2.0](https://semver.org).

Profiles version independently from Core. A profile version `<name>/<major>.<minor>` is
incremented under the same rules.

---

## 0.2.3: MCP server transport

A CHAP Coordinator can now present itself as an MCP server. Point
Claude Desktop, Cursor, Claude Code, or any other MCP client at it
and drive a CHAP workspace from natural language. Spec target: MCP
**2025-11-25** (current stable).

This is a backward-compatible addition. No wire-format changes; no
breaking changes to existing 0.2.2 consumers.

### Added

- **TypeScript MCP adapter.** New package `packages/coordinator-mcp/`
  (`@chap/coordinator-mcp`) wrapping a `Coordinator` as an MCP server.
  Built on the official `@modelcontextprotocol/sdk`. Entry point
  `makeChapMcpServer(coord, options)` returns an `Server` ready to
  attach to any MCP transport (stdio, Streamable HTTP).
- **Python MCP adapter.** New module
  `chap_coordinator.transports.mcp_server`, installable via
  `pip install chap-coordinator[mcp]`. Built on the official `mcp`
  SDK. Entry point `make_chap_mcp_server(coord, name=..., version=...)`.
- **39 CHAP methods exposed as MCP tools**, named with a `chap.`
  prefix (e.g. `chap.workspace.create`, `chap.decide.override`,
  `chap.deliberate.open`). Each tool's `inputSchema` is the JSON
  Schema for the corresponding method's params; tool descriptions
  are tuned for LLM consumption.
- **Reference stdio servers** at `reference/mcp-server-ts/` and
  `reference/mcp-server-py/`. Both wrap a Coordinator with every
  profile enabled and serve it over stdio for direct MCP-client
  consumption.
- **Five-minute walkthrough** at
  `examples/drive-chap-from-claude-desktop.md` showing how to wire
  the reference server into Claude Desktop and drive a workspace
  through natural language.
- **`Coordinator.get_workspace(workspace_id)`** convenience method on
  the Python reference (already present on TypeScript), aligning the
  two implementations' surfaces.

### Tests

- TypeScript: **8 integration tests** in `packages/coordinator-mcp/tests/`
  use the official MCP SDK's `InMemoryTransport.createLinkedPair()` to
  drive a wrapped Coordinator end-to-end, exercising every major
  profile (Core, review, routing, deliberation, audit chain).
- Python: **7 integration tests** in
  `packages/coordinator-py/tests/test_mcp_integration.py` mirror the
  TypeScript suite one-for-one using
  `mcp.shared.memory.create_connected_server_and_client_session`.
  Both suites pass.
- Both reference servers verified to handle the MCP `initialize`
  handshake and advertise all 39 tools via `tools/list`.

### Design notes

- **One Coordinator, one MCP server.** Multi-workspace is handled
  inside the Coordinator (workspaces are addressable by id); the MCP
  layer is stateless and routes every tool call through
  `coord.dispatch()`.
- **Single-sourced schemas.** Tool inputs are described by JSON
  Schemas (not Zod / Pydantic) and passed straight to the Coordinator
  without re-validation at the MCP layer. The Coordinator's own
  dispatch validates params and returns spec-correct JSON-RPC error
  codes, which the adapter surfaces as MCP tool errors.
- **Composition with the citation pattern.** The existing
  `integrations/CHAP-with-MCP.md` documents how a CHAP audit log can
  cite MCP tool calls made *inside* a workspace. The new transport
  layer adds the other direction: an external MCP client driving the
  workspace. Both patterns use the same MCP wire format underneath.
- **Auth deferred.** The adapter ships unauthenticated; MCP's OAuth
  2.1 auth model is implemented by the Streamable HTTP transport,
  layered at the HTTP boundary rather than inside the adapter. Real
  deployments add auth there.

### What's not in 0.2.3

These follow in subsequent releases:

- **A2A server transport** (CHAP-as-A2A-agent). The protocol
  alignment is there (A2A's task lifecycle maps almost 1:1 onto
  CHAP's) but the adapter and tests are deferred.
- **MCP-wrap inward helper.** A small utility that takes an MCP
  tool-call result and emits a corresponding CHAP `task.create` +
  `task.complete` pair for audit-log provenance. The pattern is
  fully documented in `integrations/CHAP-with-MCP.md`; the library
  helper is convenience.
- **A2A bridge participant.** Same story for A2A.
- **Streaming / SSE.** Synchronous request/response works; streaming
  notifications can come later if there's demand.

---



The TypeScript reference at `packages/coordinator/` is brought up to
parity with the Python reference: both now cover Core plus every
profile, 39 method handlers each.

### Added

- **TypeScript profile coverage.** `packages/coordinator/src/profiles/`
  now ships handler modules for `whisper/1.0`, `deliberation/1.0`,
  `handoff/1.0`, `control/1.0`, `routing/1.0`, `security-signed/1.0`,
  and `audit-scitt/1.0`. Plus `modes/1.0` enforcement (trial-mode
  forces review, mode-ceiling check at task.create) and
  `identity-oidc/1.0` + `identity-vc/1.0` binding hooks at
  `participant.join`. **39 method handlers in total**, matching the
  Python reference and the spec inventory exactly.
- **Supporting modules.** `canonical.ts` (JCS), `crypto.ts` (Ed25519
  via Node built-ins), `ids.ts` (deterministic-friendly ULIDs), and
  `policy.ts` (the legacy `makeDefaultPolicy` factory preserved as a
  partial-options helper).
- **62 tests** under `packages/coordinator/tests/` covering Core,
  every profile, signed-envelope verification, OIDC and VC binding,
  cross-language conformance vectors (JCS, Ed25519, chain link), and
  an end-to-end composition test exercising every method handler in
  one workspace sequence.
- **`getWorkspace`, `snapshot`, and `restore`** methods on the
  Coordinator class for persistence integrations.

### Changed (potentially breaking)

- **Wire field rename: `workspace_id` -> `workspace`.** The previous
  TypeScript library used `params.workspace_id` while the spec, the
  conformance harness, the test vectors, the standalone reference
  servers, and the Python reference all use `params.workspace`. The
  TypeScript library now uses `workspace`. Consumers of
  `@chap/coordinator` who relied on `workspace_id` must update their
  call sites. The playground at `reference/playground/` is updated
  accordingly.
- **`participant.join` field rename: `uri` -> `from`.** Same reason.
  The spec uses `from` to identify the joining participant.
- **`policy: makeDefaultPolicy(...)` -> `...makeDefaultPolicy(...)`.**
  The single `policy` slot is replaced by three separate hooks
  (`routingPolicy`, `reviewDepthPolicy`, `escalationPolicy`) on
  `CoordinatorOptions`. The `makeDefaultPolicy` factory now returns
  a partial-options object spread into the constructor argument.
- **`patch.ts` aligned to RFC 6902.** A `replace` operation against
  a non-existent path now throws, where previously it silently
  behaved like `add`. This matches the Python implementation and is
  what cross-language interop requires.

### Spec fidelity

Same audit pass as 0.2.1, applied to the TypeScript implementation:
top-level `sig` field for security-signed/1.0; `answer_option` for
whisper; `vote`/`comment` for deliberation; multi-task `tasks` for
handoff with single-recipient `to` (URI or `group:`); `scope`
parameter on control.pause/resume; snapshot as artefact; supersede
creates the successor; route_decision artefact per routing decision;
`cnf.jwk` / VP holder-key pinning on join.

### Cross-language interop verified

Both reference implementations pass the existing conformance harness
(`conformance/harness/`) on the same JSON-RPC 2.0 wire:

- TypeScript standalone server (`reference/core-plus-review/`): 21/21
- TypeScript library server (built from `packages/coordinator/`): 21/21
- Python server (`reference/python/`): 21/21

The harness currently covers Core and `review/1.0`; expanding it to
cover the other nine profiles is the next item on the road to a
normative Full conformance claim.

### Playground updated

The `reference/playground/` smoke tests are updated for the spec-correct
flow: the agent now calls `review.depth` and `escalate.auto` explicitly
after `task.complete`, assembles the reviewer set from the escalation
outcome, and opens the review via `review.request`. All seven
playground smoke tests pass. A new `reference/playground/src/policies.ts`
module supplies the playground-specific routing policy used to
demonstrate the routing/1.0 profile end-to-end.

The artefact's `outcome` field for `escalate.auto` is now a structured
`{ escalate, to? }` object rather than the bare strings `"escalated"` /
`"no_escalation"`; the wire response shape (in the JSON-RPC `result`)
is unchanged. The Python and TypeScript references are both updated.

---



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
