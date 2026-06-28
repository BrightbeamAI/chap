# Changelog

All notable changes to the Collaborative Human-Agent Protocol (CHAP) will be recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the protocol
adheres to [Semantic Versioning 2.0](https://semver.org).

Profiles version independently from Core. A profile version `<name>/<major>.<minor>` is
incremented under the same rules.

---

## 0.2.6: MCP argument coercion, dual-language tour, and authorisation enforcement

Follows the 0.2.5 adoption release. Three things: a real-world MCP
integration fix, a clearer README walkthrough, and an authorisation
tightening reported by a collaborator. Backward-compatible on the wire:
no envelope or schema changes. The authorisation work changes behaviour
(it rejects envelopes that were silently accepted before), so it lands
as a minor version rather than a patch.

### Fixed

- **MCP adapters coerce stringified-JSON arguments.** A real Claude
  Desktop integration surfaced that LLM MCP clients routinely serialise
  structured tool arguments as JSON-encoded strings rather than native
  objects or arrays. That left an artefact stored as a string, which
  then crashed a `decide.override` object-path patch with an internal
  error (-32603). Both the TypeScript and Python MCP adapters now
  normalise these at the adapter boundary, before the envelope reaches
  the protocol core: a string value whose parameter schema admits an
  object/array type is JSON-parsed when, and only when, it parses
  cleanly to an accepted type. Bare strings the schema accepts as
  strings (participant URIs, task ids, rationales) are left untouched.
  The protocol core is unchanged and stays strict; the audit log now
  records correctly-typed artefacts and the override applies on the
  first try. Tool descriptions for `output`, `artefact`, and `to` now
  state explicitly that a JSON value is expected, not a stringified one.
- **Actor membership is now enforced.** Before this release, only a
  task's *assignee* was checked for membership (at `task.create` /
  `task.route`); the *actor* (`from`) of a method was not. A decision,
  completion, or review request could therefore be attributed to a
  participant who had never joined. The error table (§13.3) defined an
  `unknown_participant` code for this condition, but no normative
  precondition stated it and no implementation enforced it. Every
  actor-action method in Core and `review/1.0` (`task.complete`,
  `review.request`, `decide.approve`, `decide.reject`, `decide.override`,
  `abstain.declare`) now verifies that `from` is a joined member and
  rejects a non-member with `not_authorised` (-32011). Applied
  identically in the TypeScript coordinator, the Python coordinator, and
  the standalone `core-plus-review` reference server. New precondition
  text added at SPECIFICATION.md §6.3.1. Reported by a collaborator
  integrating CHAP over MCP.

### Added

- **Reviewer-set eligibility (review/1.0).** To act on a review,
  `decide.*` and `abstain.declare` now require `from` to be one of the
  reviewers the review was addressed to (the `to` set on
  `review.request`), not merely any member. The `rule` field still
  governs *how many* must decide; the `to` set governs *who is eligible*.
  A review addressed to a broadcast scope (`workspace:<id>` or
  `group:<id>`) admits any member (resp. any group member); a review
  with no recorded reviewer set falls back to the membership floor. This
  is a new normative rule for the profile, surfaced via the `-32011`
  code review.md already defined. See profiles/review.md §3.2.
- **Worked authorisation walkthrough** at
  `packages/coordinator-py/examples/authorisation_walkthrough.py`:
  exercises an allowed approve and override plus the two refused paths
  (non-member, and member-not-in-reviewer-set), each rejected with
  -32011.
- **Conformance vectors `rv-07` and `rv-08`** covering the non-member
  and non-reviewer rejections; the harness now runs 23 vectors.

### Changed

- **README 90-second tour rewritten as dual-language.** The walkthrough
  now shows TypeScript (typed facade) and Python (dict-based dispatch)
  side by side, and the hero GIF was rebuilt with a step indicator and a
  progress bar so the six-step Core+review flow is legible. Documentation
  only; no API change.
- **Docs updated for the authorisation model.** New SPECIFICATION.md
  §6.3.1 (actor-membership precondition); profiles/review.md §3.2
  (reviewer-set eligibility, with the broadcast-scope caveat); a HANDBOOK
  §7.5 on what the Coordinator enforces beneath workspace policy; FAQ,
  ARCHITECTURE (authorisation layering), and GLOSSARY (Actor, Break-glass,
  Reviewer set) entries.

### Notes

- `escalate.raise` already required its escalation target to be a member,
  so it was unchanged. No break-glass machinery is introduced; admitting
  a new actor is done by joining first, which records the entry as its
  own audit event (flagged-join is the recommended future pattern).
- The reference implementations surface the membership and reviewer-set
  conditions with `not_authorised` (-32011) rather than the spec table's
  `unknown_participant` (-32403), because -32403 already denotes
  `OIDC_TOKEN_INVALID` in their private error range. The broader
  spec-vs-implementation error-table reconciliation is tracked
  separately and is out of scope here.
- The MCP coercion fix is scoped to the adapter boundary; the same
  stringified-JSON input reaching the core through a non-adapter path
  still produces -32603, a latent core rough edge left for a separate
  change.

### Tests

- TS coordinator: **95** (+11 authorisation), TS MCP: **17** (+9 coercion),
  TS A2A: 14, TS playground: 7
- Python coordinator: **120** (+9 coercion, +11 authorisation),
  Python langgraph: 10
- Conformance harness: **23/23** on both reference implementations
  (+2 authorisation vectors)

---

## 0.2.5: publish-ready packages, persistent storage, typed facade, framework adapter

The "adoption" release. The protocol was already there; this release closes
the gap between "impressive spec" and "I had it running in my agent before
lunch". Backward-compatible: no wire-format or schema changes.

### Added

- **Publish-ready npm packages.** `@chap/coordinator`, `@chap/coordinator-mcp`,
  and `@chap/coordinator-a2a` now build to `dist/` (ESM + CJS + `.d.ts` +
  source maps via `tsup`), declare `exports` maps, and ship `prepublishOnly`
  that runs the schemas-drift check, typecheck, tests, and build.
- **PyPI-ready Python wheel.** `chap-coordinator` builds cleanly with a
  `py.typed` marker for type-checker consumers (PEP 561).
- **Pluggable storage with SQLite backend.** New `Store` interface in
  both languages; `MemoryStore` is the default, `SqliteStore` persists
  workspaces to disk and rehydrates on coordinator construction. The
  audit chain head survives restart. TypeScript uses `better-sqlite3`
  as an optional dep; Python uses the stdlib `sqlite3` module (no
  external dep needed). Both write the same schema so a database file
  from one implementation can be read by the other.
- **Typed method facade.** `coord.api.task.create({...})`,
  `coord.api.decide.override({...})`, and equivalents for all 39 methods.
  Full autocomplete and compile-time checking. The original
  `dispatch(envelope)` path is unchanged and still recommended for tools
  that build envelopes by other means.
- **`chap-langgraph`** package (Python). Bridges LangGraph's
  human-in-the-loop interrupt boundary into CHAP envelopes
  (`task.complete` + `review.request`, then `decide.approve` /
  `decide.reject` / `decide.override` on resume). LangGraph itself is
  optional; the bridge accepts any dict-shaped state.
- **Schema-drift detection.** `npm run check:schemas` enforces parity
  between the JSON-schema method catalogue and the TypeScript
  `MethodTable`. Caught and fixed 22 stale `spec-only` entries while
  landing it.
- **Zero-install playground.** `Dockerfile` + `docker-compose.yml`
  (one-command demo, bound to `127.0.0.1`). `.devcontainer/` config for
  Codespaces. New `CHAP_NO_LLM=1` deterministic mock-drafter mode in the
  playground so the marquee demo runs anywhere without a model download.
- **Audit/override viewer.** `tools/audit-viewer.html`: single-file HTML
  with no build step, no dependencies, no network. Drop a `snapshot()`
  JSON; see hash-chain integrity, method-frequency bars, override-tag
  bars, and the full chain rendered inline. Hardened with CSP and
  consistent HTML escaping.
- **Reusable conformance GitHub Action** at
  `.github/actions/chap-conformance/`. Other repos can drop in
  `uses: BrightbeamAI/chap/.github/actions/chap-conformance@v0.2.5` to
  get a "CHAP-conformant" badge.
- **Implementation registry** at `IMPLEMENTATIONS.md` (the long-promised
  link from `ABOUT.md`).
- **Root `package.json`** with `npm workspaces` so the monorepo is
  installable with one `npm install`.

### Changed

- **README quickstart fixed.** Previous version called a non-existent
  `storage` option, used the wrong param names (`workspace_id`/`uri`
  instead of `workspace`/`from`/`type`), and referenced an
  `npx @chap/analyze-overrides` package that did not exist. All three
  issues fixed; the snippet now runs against the real shipped library.
- **`examples/00-five-minute-start.md`** literal `{ ... same as above ... }`
  placeholder mid-flow replaced with the real payload.
- **`analyze-overrides.ts`** gained a `--db <path>` flag so the in-process
  SqliteStore quickstart works without spinning up the HTTP server.

### Tests

- TS coordinator: **84** (was 72; +6 storage, +6 typed facade)
- TS MCP: 8, TS A2A: 14, TS playground: 7
- Python coordinator: **100**, Python langgraph: **10** (new)
- Conformance harness: 21/21 on both references

### Security

- Audit viewer hardened: every user-controlled field that lands in
  `innerHTML` is now passed through `escapeHtml`. Restrictive CSP
  (`connect-src 'none'`, `frame-ancestors 'none'`) limits the blast
  radius even if a future innerHTML site slips through.
- Docker playground bound to `127.0.0.1` only.
- SqliteStore uses prepared statements with bound parameters; no string
  interpolation into SQL.
- `chap-langgraph` idempotency rewritten to use a structural
  post-condition check rather than matching error-message strings.

### What's not in 0.2.5

Streaming/SSE transports, A2A push notifications, MCP Streamable HTTP,
and A2A 1.0 in the TypeScript adapter (awaits `@a2a-js/sdk` upstream
upgrade) all carry forward as deferred items.

---

## 0.2.4: A2A server transport + inward wrap helpers

Third leg of the transport story. A Coordinator can now present itself as
an [A2A](https://a2a-protocol.org) agent, complementing the MCP server
transport from 0.2.3. Backward-compatible.

### Added

- **TypeScript A2A adapter** (`@chap/coordinator-a2a`) on `@a2a-js/sdk`
  (A2A 0.3.0). `makeChapAgentCard(...)` returns an Agent Card with 39
  skills, one per CHAP method, named `chap.<method>`.
  `makeChapAgentExecutor(coord)` returns an `AgentExecutor`.
- **Python A2A adapter** (`chap_coordinator.transports.a2a_server`) on
  `a2a-sdk` 1.x (A2A 1.0, with v0.3 compatibility enabled). Same surface
  as the TypeScript adapter.
- **Reference A2A servers** at `reference/a2a-server-ts/` (Express) and
  `reference/a2a-server-py/` (FastAPI). Verified end-to-end with real
  HTTP.
- **Inward wrap helpers** (`wrapMcpToolCall`, `wrapA2aMessageExchange`,
  `contentHash`) in both languages: take a completed external event and
  emit the matching CHAP audit entries with input/output hashes.
- **Walkthrough**: `examples/drive-chap-from-an-a2a-orchestrator.md`.
- Documentation updates across `ABOUT.md`,
  `RELATIONSHIP-TO-OTHER-STANDARDS.md`, `ARCHITECTURE.md`,
  `SPECIFICATION.md` §16.3, `FAQ.md`, `GLOSSARY.md`.

### Spec version asymmetry

The Python `a2a-sdk` is at A2A 1.0, the TypeScript `@a2a-js/sdk` is at
A2A 0.3.0. The CHAP adapter layer is identical across both; Agent Cards
advertise the correct version per implementation.

### Tests

TS A2A: 14. Python A2A: 10. Wrap helpers: 10 each. Both reference
servers smoke-tested with `curl`.

---

## 0.2.3: MCP server transport

A Coordinator can now present itself as an MCP server. Point Claude
Desktop, Cursor, Claude Code, or any other MCP client at it and drive a
CHAP workspace from natural language. Spec target: MCP 2025-11-25.
Backward-compatible.

### Added

- **TypeScript MCP adapter** (`@chap/coordinator-mcp`) on
  `@modelcontextprotocol/sdk`.
- **Python MCP adapter** (`chap_coordinator.transports.mcp_server`) on
  the official `mcp` SDK, installable via `pip install chap-coordinator[mcp]`.
- **39 CHAP methods exposed as MCP tools** named `chap.<method>`. Tool
  `inputSchema` is the JSON Schema for the method's params.
- **Reference stdio servers** at `reference/mcp-server-{ts,py}/`.
- **Walkthrough**: `examples/drive-chap-from-claude-desktop.md`.
- **`Coordinator.get_workspace(...)`** convenience on the Python
  reference, aligning the two implementations' surfaces.

### Tests

TS MCP: 8 integration tests via `InMemoryTransport.createLinkedPair()`.
Python MCP: 7 integration tests via
`mcp.shared.memory.create_connected_server_and_client_session`. Both
reference servers verified via `initialize` handshake + `tools/list`.

---

## 0.2.2: TypeScript profile parity

The TypeScript reference at `packages/coordinator/` is brought up to
parity with the Python reference: both now cover Core plus every profile,
39 method handlers each.

### Added

- TS handlers for `whisper/1.0`, `deliberation/1.0`, `handoff/1.0`,
  `control/1.0`, `routing/1.0`, `security-signed/1.0`, `audit-scitt/1.0`.
  Plus `modes/1.0` enforcement and `identity-oidc/1.0` / `identity-vc/1.0`
  binding hooks at `participant.join`.
- Supporting modules: `canonical.ts` (JCS), `crypto.ts` (Ed25519 via Node
  built-ins), `ids.ts`, `policy.ts`.
- 62 tests including JCS and Ed25519 conformance vectors, signed-envelope
  verification, OIDC/VC binding, and a composition test exercising every
  method handler.
- `getWorkspace`, `snapshot`, and `restore` methods on the Coordinator
  for persistence integrations.

### Changed (potentially breaking for `@chap/coordinator` consumers only)

- Wire field rename: `workspace_id` → `workspace`. Matches the spec, the
  Python reference, the conformance harness, and the test vectors.
- `participant.join` field rename: `uri` → `from`.
- `policy: makeDefaultPolicy(...)` slot replaced by separate
  `routingPolicy`, `reviewDepthPolicy`, `escalationPolicy` hooks.
- `patch.ts` aligned to RFC 6902: `replace` against a non-existent path
  now throws (matching Python).

### Cross-language interop verified

All three configurations pass the same 21-vector conformance harness:
TypeScript standalone server, TypeScript library server, Python server.

---

## 0.2.1: Python reference implementation

A second reference implementation, in Python. No protocol or wire-format
changes.

### Added

- `packages/coordinator-py/` (`chap-coordinator`). Core plus every profile,
  39 method handlers, transport-agnostic library.
- `reference/python/`: HTTP server, demo client, `analyze_overrides.py`.
  Passes the same conformance harness as the TypeScript reference on the
  same JSON-RPC 2.0 wire.
- 63 tests covering Core, every profile, cryptographic test vectors,
  signed-envelope verification, OIDC and VC binding, and end-to-end
  composition.

### Notes

- Zero required runtime dependencies for Core. The `security-signed/1.0`
  profile needs `cryptography>=42` via `pip install "chap-coordinator[crypto]"`.
- The Python implementation closes the second-interoperable-implementation
  prerequisite for the Full conformance level.

---

## 0.2: First public release

The first public release of CHAP. A working draft suitable for review,
experimentation, and early production pilots; not yet a stable 1.0.

### Contents

- **Core.** A JSON-RPC 2.0 envelope and seven methods
  (`workspace.describe`, `participant.join`, `participant.leave`,
  `task.create`, `task.update`, `task.complete`, `audit.read`). Task
  lifecycle, participant model, append-only evidence log.
- **Eleven profiles.** `review`, `modes`, `routing`, `whisper`,
  `deliberation`, `handoff`, `control`, `identity-oidc`, `identity-vc`,
  `security-signed`, `audit-scitt`.
- **TypeScript reference implementation.** Core, Core+Review, the
  coordinator package, a CLI, an override-analytics tool, and a
  two-participant playground.
- **Conformance harness** with 21 test vectors covering wire format, all
  seven Core methods, and the six Review methods. Two conformance levels
  claimable (Minimal, Recommended).
- **Twelve worked scenarios** in [`IN_PRACTICE.md`](./IN_PRACTICE.md),
  spanning a solo developer through GMP-regulated manufacturing.
- **Documentation**: Specification, Handbook, Architecture, Security,
  FAQ, Glossary, and a relationship mapping to other standards.

---

## Versioning policy

- **MAJOR** (`X.0`): wire-breaking changes; old clients cannot talk to new
  servers. Migration windows of at least one calendar year between MAJOR
  versions.
- **MINOR** (`X.Y`): additive only at the protocol level. New methods, new
  optional fields, new error codes. Old clients keep working.
- **PATCH** (`X.Y.Z`): editorial fixes and implementation-side additions.
  Wire format and schemas unchanged.

Profiles version independently from Core. A workspace declares the specific
Core version and the specific profile versions it implements via
`workspace.describe`'s `profiles` field.
