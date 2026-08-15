# CHAP 0.2.9: complete package publish and consolidated 0.2.x hardening

First release with the full set on npm and PyPI: the MCP and A2A coordinators
(`@brightbeamai/chap-coordinator-mcp`, `-a2a`) and all five framework adapters
(`chap-langgraph`, `chap-pydantic-ai`, `chap-llama-index`, `chap-ag2`,
`chap-google-adk`), previously unpublished, now ship alongside the coordinators.
It also consolidates the security, audit-integrity, and robustness work that
landed across the 0.2.x line. Both coordinators remain at parity. The CHAP
wire format is unchanged; the MCP transports change observable behaviour,
detailed under MCP 2026-07-28 below.

### Security

- **Key lifecycle bound to signatures.** `participant.rotate_key` must be signed
  with the old key under `require_signatures` (`SIG_ROTATION_KEY_MISMATCH`
  otherwise); `participant.revoke_key` is gated to the key's owner or an
  admin-role member. (#61, #62)
- **`participant.join` no longer replaces a member.** Re-joins are additive; only
  attested (OIDC/VC-bound) keys merge into an existing member, and self-asserted
  keys are not co-registered alongside a proof-of-possession binding. (#25, #37)
- **Step-up fixed and scoped.** Fails closed for OIDC actors and enforces a
  configurable `min_acr`, while agents and services authenticating out of band
  (SPIFFE/X.509) are correctly exempt. (#63)
- **Membership floors on mutating methods.** `task.create` and `task.update`
  require membership; `workspace.set_profiles` requires the admin role;
  `escalate.raise` requires membership and a non-terminal task. (#64, #32)
- **Adapters can no longer fabricate decisions:** no default-to-approve, no
  forced-human reviewer, unknown identity schemes rejected rather than treated as
  human. (#57)
- **SCITT verification fails closed** when no receipt verifier is configured. (#27)
- **Optional read authorisation.** `require_read_membership` (default off) gates
  `audit.read` and `workspace.describe` for multi-tenant or directly-exposed
  deployments. (#64)

### Fixed

- **The whisper lapse notification carries `task_id`.** A lapsed whisper
  applies its default with no human input; it is now visible on a
  task-filtered `audit.read` rather than only under the whisper id.
- **`whisper.answer` records its `task_id`.** The answer previously carried only
  `whisper_id`, so an `audit.read` filtered by `task_id` returned the ask without
  the answer. The Coordinator now records the answer against the task held on the
  whisper (overwriting any caller-supplied value) and echoes `task_id` in the
  response, as `profiles/whisper.md` section 3 already specified.
- **Reads no longer mutate the audit chain.** `audit.read`, `workspace.describe`,
  `audit.verify_chain`, and `audit.verify_receipt` are no longer recorded, so
  inspecting or verifying the log no longer changes it. (#43)
- **Chain verification** refuses to run without chaining enabled, verifies from
  genesis, and no longer reports an unchained workspace as tampered. (#23)
- **Overrides** diff against the artefact under review, not a caller value. (#41)
- **Cross-implementation hashing.** Canonical object keys sorted by UTF-16 code
  unit (JCS); `crypto.sign` emits the `ed25519:<kid>:<b64>` wire tag. (#53, #51)
- **Restart-safety.** `Member.keys` and `Task.review` rehydrate on load, so signed
  workspaces and in-flight reviews survive a restart; a corrupt persistence row is
  skipped instead of dropping every workspace. (#39, #49)
- **Deliberation vote weights** taken from the opener, not self-declared by the
  voter. (#30)
- **Decision tags** validated as a list of strings. (#35)

### Added

- **Optional `idempotency_key` on `task.create`:** a redelivered create carrying a
  seen key returns the original task with no duplicate and no second audit entry;
  unchanged when no key is supplied. (#68)
- Regression tests across the dispatch, join, and canonicalisation fixes. (#28)

### Hardened

- **JSON Patch** op-count and result-size bounds guard against amplification and
  copy-bomb inputs, with a shared conformance reject vector. (#55)
- **Python reference server** hardened against malformed requests: invalid
  `Content-Length`, unbounded reads, and deeply nested bodies. (#59)

### MCP 2026-07-28

Both MCP adapters now target the 2026-07-28 revision and continue to serve
2025-11-25 clients on the same server. The 2026 revision is stateless: rather
than negotiating once through an `initialize` handshake, every request carries
its protocol version and client capabilities in `_meta`, and the server accepts
or rejects each request independently.

- **`server/discover` implemented (SEP-2575)** in both languages, answering a
  discovery probe with the supported protocol versions, capabilities and
  identity in one request. It advertises `["2026-07-28", "2025-11-25"]`,
  spanning both eras because both are served. The method belongs to the 2026
  vocabulary, so a request carrying no per-request envelope is answered with
  `MethodNotFound`.
- **Per-request version negotiation.** A request declaring a protocol version
  the adapter does not implement is refused with `UnsupportedProtocolVersion`
  (-32022), carrying the versions that may be declared so a retry cannot land
  on the same refusal. Only `2026-07-28` is declarable per request; 2025-11-25
  is reached through `initialize`, as its own revision defines.
- **Envelope validation.** A request declaring a protocol version must also
  carry `io.modelcontextprotocol/clientCapabilities`; a missing required field
  is refused with `InvalidParams` (-32602), as is a non-string version.
- **Results are typed and cacheable (SEP-2322, SEP-2549).** A 2026-era caller
  receives `resultType: "complete"` on every result, and `ttlMs` with
  `cacheScope` on `tools/list` and `server/discover`. A 2025-era caller
  receives the result shape its own revision defines, without the 2026 fields.
- **The Python transport builds on the `mcp` 2.x SDK**, which implements the
  2026 boundary natively; the `mcp` extra is now `>=2,<3`. The TypeScript
  adapter implements the same rules against SDK 1.x, which predates the
  revision. Both were verified against the same probe suite and answer it
  identically.

### Dependencies

- Bump `hono`, `fast-uri`, `@hono/node-server`, and `@modelcontextprotocol/sdk`.
  (#66, #67, #70)

### Packaging

- npm packages under the `@brightbeamai/` scope; the full nine-package set now
  published on npm and PyPI. Stopped tracking Python bytecode caches. (#60)

