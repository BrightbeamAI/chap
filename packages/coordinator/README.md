# @brightbeamai/chap-coordinator (TypeScript)

TypeScript reference implementation of the Collaborative Human-Agent Protocol (CHAP).

Covers Core plus every profile, 39 method handlers in total. Spec-aligned
field names, error codes, and response shapes against `profiles/*.md`.
The Python reference at `packages/coordinator-py/` implements the same
surface, so the two interoperate on the same JSON-RPC 2.0 wire.

## Install

This package is currently distributed alongside the spec repo rather
than published to npm. To use it directly from source in another
TypeScript project:

```bash
# From the chap repo root
cd packages/coordinator
npm pack
# In your project
npm install /path/to/chap-coordinator-0.2.5.tgz
```

Node 18+ required. Zero external runtime dependencies; uses Node's
built-in `crypto` for Ed25519 and JCS.

## Companion packages

The `@brightbeamai/chap-coordinator` package is the protocol core. Two transport
adapters ship alongside it for embedding a Coordinator in MCP and
A2A ecosystems:

- [`@brightbeamai/chap-coordinator-mcp`](https://github.com/BrightbeamAI/chap/tree/main/packages/coordinator-mcp) wraps a Coordinator
  as an MCP server. Every CHAP method becomes an MCP tool named
  `chap.<method>`. Reference stdio server at
  `reference/mcp-server-ts/`. Spec target: MCP 2025-11-25.
- [`@brightbeamai/chap-coordinator-a2a`](https://github.com/BrightbeamAI/chap/tree/main/packages/coordinator-a2a) wraps a Coordinator
  as an A2A agent. Every CHAP method becomes an `AgentSkill` on the
  Agent Card. Reference Express server at `reference/a2a-server-ts/`.
  Spec target: A2A 0.3.0.

Inward citation helpers (`wrapMcpToolCall`, `wrapA2aMessageExchange`,
`contentHash`) are exported from `@brightbeamai/chap-coordinator` directly. They
take a completed external event and emit the matching CHAP audit
entries with input/output hashes.

## Quick start

```typescript
import { Coordinator } from "@brightbeamai/chap-coordinator";

const coord = new Coordinator({
  defaultProfiles: ["core/1.0", "review/1.0", "whisper/1.0"],
});

coord.dispatch({
  jsonrpc: "2.0", id: "1",
  method: "workspace.create",
  params: { workspace: "wsp_demo" },
});

coord.dispatch({
  jsonrpc: "2.0", id: "2",
  method: "participant.join",
  params: { workspace: "wsp_demo",
            from: "human:me@local", type: "human", role: "reviewer" },
});

coord.dispatch({
  jsonrpc: "2.0", id: "3",
  method: "participant.join",
  params: { workspace: "wsp_demo",
            from: "agent:bot", type: "agent", role: "drafter" },
});

const resp = coord.dispatch({
  jsonrpc: "2.0", id: "4",
  method: "task.create",
  params: {
    workspace: "wsp_demo",
    from: "human:me@local",
    kind: "draft_response",
    input: { ticket_id: "INC-1" },
    assignee: "agent:bot",
  },
});

console.log(resp.result);  // { task_id: "tsk_...", state: "created" }
```

## What is implemented

| Profile               | Methods (per profile spec)                                                                |
|-----------------------|-------------------------------------------------------------------------------------------|
| `core/1.0`            | `workspace.create`, `workspace.describe`, `workspace.set_profiles`, `participant.join`, `participant.leave`, `task.create`, `task.update`, `task.complete`, `audit.read` |
| `review/1.0`          | `review.request`, `decide.approve`, `decide.reject`, `decide.override`, `abstain.declare`, `escalate.raise` |
| `whisper/1.0`         | `whisper.ask`, `whisper.answer`; lapse hook via `coord.checkWhisperLapses(workspaceId, now?)` |
| `deliberation/1.0`    | `deliberate.open`, `deliberate.comment`, `deliberate.vote`, `deliberate.close` |
| `handoff/1.0`         | `handoff.propose`, `handoff.accept`, `handoff.decline`; multi-task; recipient may be a URI or `group:` |
| `control/1.0`         | `control.pause`, `control.resume`, `control.cancel`, `control.snapshot`, `control.rollback`, `control.supersede`, `control.set_mode_ceiling`; pause/resume take `scope: task/participant/workspace` |
| `routing/1.0`         | `task.route`, `review.depth`, `escalate.auto`; each emits a `route_decision` artefact |
| `modes/1.0`           | Mode handling at `task.create`; `trial` mode forces `review_required`; `control.set_mode_ceiling` shared with control/1.0 |
| `security-signed/1.0` | `participant.rotate_key`, `participant.revoke_key`; top-level `sig` verified at dispatch when `requireSignatures` is true |
| `audit-scitt/1.0`     | `audit.submit_to_scitt`, `audit.verify_receipt`, `audit.verify_chain`; SCITT statements assembled and passed to a deployment-supplied submitter; local prev-hash chain linkage retained |
| `identity-oidc/1.0`   | `participant.join` binding via `verifyOidcToken`; `cnf.jwk` pinning, step-up auth via `enforceStepUp` |
| `identity-vc/1.0`     | `participant.join` binding via `verifyVc`; holder-key pinning |

**39 method handlers in total**, matching the Python reference exactly.

## Architecture

The coordinator is transport-agnostic, persistence-agnostic, and
UI-agnostic. The entry point:

```typescript
const response = coord.dispatch(envelope);
```

`envelope` is a JSON-RPC 2.0 request; `response` is the JSON-RPC 2.0
response. State is held in memory; persist by subscribing with
`coord.onAudit(listener)`.

Reference HTTP bindings:
- `reference/core/` and `reference/core-plus-review/` (standalone TS
  servers used by the conformance harness and the demo playground)
- `reference/python/` (HTTP server using the Python reference)

## Determinism for tests

```typescript
const coord = new Coordinator({
  deterministicIds: true,
  deterministicClock: true,
});
```

ULIDs derive from a deterministic counter; the clock advances by a
fixed step per emission. Useful for golden-file tests.

## Identity binding

```typescript
const coord = new Coordinator({
  verifyOidcToken: (token) => {
    // Validate against your IdP; return claims dict or null.
    // Include "cnf": { "jwk": {...} } to bind a signing key.
    return { sub: "...", auth_time: 1234, cnf: { jwk: {...} } };
  },
  verifyVc: (vp) => {
    // Verify the VP; return subject claims or null.
    // Include "cnf_jwk": {...} to bind a holder key.
    return { holder: "did:example:alice", cnf_jwk: {...} };
  },
  enforceStepUp: true,  // require fresh auth_time for privileged methods
});
```

If a `participant.join` envelope includes `oidc_token` or
`vc_presentation`, the relevant hook is called and the bound key is
added to the participant's key history.

## Signed envelopes (security-signed/1.0)

```typescript
const coord = new Coordinator({ requireSignatures: true });
```

Each participant registers one or more JWKs at `participant.join` via
`jwks: { keys: [...] }`. Senders sign the JCS canonicalisation of the
envelope **with the top-level `sig` field removed**, then attach the
signature as a top-level `sig: "ed25519:<kid>:<base64>"` field. The
Coordinator looks up the verifying key by `(from, kid, ts)`, so
historical envelopes verify across key rotation.

Key lifecycle:
- `participant.rotate_key` is signed with the old key; the Coordinator
  marks `valid_until` on the old key and `valid_from` on the new one.
- `participant.revoke_key` is an admin operation; envelopes signed
  with the revoked key after revocation are rejected.

Helpers exported from `@brightbeamai/chap-coordinator/crypto`:

```typescript
import {
  deriveKeypair,         // demo: deterministic keypair from a URI
  signEnvelope,          // sign canonical bytes -> ed25519:<kid>:<b64>
  verifyEnvelope,        // verify a sig tag against canonical + pubkey
  publicKeyFromJwk,      // JWK -> Node KeyObject
} from "@brightbeamai/chap-coordinator";
```

## Routing policies (routing/1.0)

A default policy ships out of the box. Override it with operator hooks:

```typescript
const coord = new Coordinator({
  routingPolicy: (task, candidates) => ({
    selected: candidates[0],
    rationale: {
      policy_id: "round-robin",
      summary: "first eligible",
    },
  }),
  reviewDepthPolicy: (task, hints) => ({
    depth: "spot_check",
    sampling_probability: 0.05,
    rationale: { policy_id: "v1", summary: "5% sample" },
  }),
  escalationPolicy: (task, hints) => ({
    escalate: hints.criticality === "critical",
    to: "group:senior-pool",
    triggered_rule: { rule_id: "esc-crit", summary: "criticality=critical" },
  }),
});
```

Every call to `task.route`, `review.depth`, and `escalate.auto`
emits a `route_decision` artefact. `task.route` also updates the
task's `assignee`.

## Audit and SCITT (audit-scitt/1.0)

Audit entries are hash-linked when the profile is active.
`audit.verify_chain` replays the chain locally and confirms
`prev_hash` continuity.

`audit.submit_to_scitt` builds a SCITT-shaped signed statement for
each audit entry in the requested range and passes it to a
deployment-supplied submitter:

```typescript
const coord = new Coordinator({
  scittSubmitter: (statement) => {
    // Submit to your SCITT transparency service; return the receipt
    return { receipt_id: "...", log_root: "..." };
  },
});
```

Without a submitter configured, `audit.submit_to_scitt` returns the
statements so the caller can submit out-of-band.

## Testing

```bash
npm test
```

72 tests as of this release, covering Core, every profile, JCS and
Ed25519 conformance vectors, signed-envelope verification, OIDC/VC
binding, the inward wrap helpers, and an end-to-end composition test
exercising every method handler in one workspace.

## Spec fidelity

This implementation was reviewed against every profile spec under
`profiles/` and aligned with the documented field names, error codes,
and response shapes. The full audit notes are in the parent repo's
[`CHANGELOG.md`](https://github.com/BrightbeamAI/chap/blob/main/CHANGELOG.md), with the 0.2.2 entry covering
the original TypeScript expansion and 0.2.3 / 0.2.4 covering the MCP
and A2A transport additions.

## License

Apache 2.0. See the parent repository's [LICENSE](./LICENSE).

## Specification

Tracks the spec at
[github.com/BrightbeamAI/chap/SPECIFICATION.md](https://github.com/BrightbeamAI/chap/blob/main/SPECIFICATION.md).
