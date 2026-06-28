# CHAP Implementations

This is the public registry of CHAP coordinator implementations.
Interoperability is the credibility currency of a standard; this
registry exists so anyone building on CHAP can see who else is, what
spec version each implementation targets, what surface they cover,
and whether they pass the published conformance harness.

> **Adding an entry**: open a PR against this file with a row in the
> "Implementations" table below and a brief note in the "Notes by
> implementation" section. Include a link to a conformance run output
> (the artifact produced by the
> [`chap-conformance` Action](.github/actions/chap-conformance/) is
> the preferred format).

## Implementations

| Name                        | Language    | CHAP version | Profile surface | Conformance | Status      | License      | Authors            |
| --------------------------- | ----------- | ------------ | --------------- | ----------- | ----------- | ------------ | ------------------ |
| `@chap/coordinator`         | TypeScript  | 0.2.5        | Full v0.2 (39 methods) | 23/23 passing on canonical harness | Stable | Apache-2.0 | Brightbeam AI |
| `chap-coordinator` (Python) | Python 3.10+ | 0.2.5       | Full v0.2 (39 methods) | 23/23 passing on canonical harness | Stable | Apache-2.0 | Brightbeam AI |
| `@chap/coordinator-mcp`     | TypeScript  | 0.2.5        | All 39 methods as MCP tools | Adapter, inherits underlying coordinator's score | Stable | Apache-2.0 | Brightbeam AI |
| `@chap/coordinator-a2a`     | TypeScript  | 0.2.5        | All 39 methods as A2A skills | Adapter, inherits underlying coordinator's score | Stable | Apache-2.0 | Brightbeam AI |
| `chap-langgraph`            | Python 3.10+ | 0.2.5       | Bridge: 8 methods exercised on the HIL path | 10/10 bridge tests | Beta | Apache-2.0 | Brightbeam AI |

## Notes by implementation

### `@chap/coordinator` (TypeScript reference)

The protocol as a library. Embed it in a Node service, drive it
directly from a script, or wire it under one of the transport
adapters. Covers Core, review/1.0, whisper/1.0, deliberation/1.0,
handoff/1.0, control/1.0, routing/1.0, security-signed/1.0, and
audit-scitt/1.0. Zero runtime dependencies for Core. Optional
`better-sqlite3` for persistent storage.

Package: [`packages/coordinator/`](./packages/coordinator/) ·
Conformance: passes the 21-vector v0.2 harness on the same JSON-RPC
2.0 wire as the Python reference.

### `chap-coordinator` (Python reference)

Independent implementation of the same protocol surface. Interops
with the TypeScript reference on the canonical wire. Profile coverage
identical. Includes the same wrap-helper conveniences and the MCP/A2A
server transport adapters as Python modules.

Package: [`packages/coordinator-py/`](./packages/coordinator-py/) ·
Conformance: passes the same 21-vector v0.2 harness as the TypeScript
reference.

### `@chap/coordinator-mcp` (MCP transport adapter)

Wraps a Coordinator as an MCP server. Every CHAP method becomes an
MCP tool named `chap.<method>`. Spec target: MCP 2025-11-25. Stateless
adapter; correctness is fully inherited from the underlying
Coordinator.

Package: [`packages/coordinator-mcp/`](./packages/coordinator-mcp/) ·
Walkthrough: [`examples/drive-chap-from-claude-desktop.md`](./examples/drive-chap-from-claude-desktop.md).

### `@chap/coordinator-a2a` (A2A transport adapter)

Wraps a Coordinator as an A2A agent. Every CHAP method becomes an
`AgentSkill` on the published Agent Card. Spec target: A2A 0.3.0
(TypeScript SDK) / A2A 1.0 (Python SDK). Adapter layer identical
across both.

Package: [`packages/coordinator-a2a/`](./packages/coordinator-a2a/) ·
Walkthrough: [`examples/drive-chap-from-an-a2a-orchestrator.md`](./examples/drive-chap-from-an-a2a-orchestrator.md).

### `chap-langgraph` (LangGraph bridge)

Glue between LangGraph workflows and a CHAP coordinator. Turns
LangGraph's `interrupt()` + `Command(resume=...)` cycle into the
CHAP `task.complete` + `review.request` + `decide.*` sequence so
every human-in-the-loop checkpoint becomes a hash-linked audit
entry. LangGraph itself is an optional dependency.

Package: [`packages/chap-langgraph/`](./packages/chap-langgraph/) ·
Examples: [`packages/chap-langgraph/examples/`](./packages/chap-langgraph/examples/).

## Wanted

The registry is open to any implementation that follows the wire
format and passes the harness. Particularly welcome:

- Independent Python implementation (a third reference, not built
  from the canonical Python reference)
- Rust implementation
- Go implementation
- Production deployments behind authenticated transports (mTLS,
  OIDC step-up, DPoP), with a published architecture writeup
- Domain-specific profile contributions (e.g., a healthcare profile,
  a financial-services profile)
