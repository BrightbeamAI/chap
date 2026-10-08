# CHAP Implementations

This page lists the known implementations of CHAP: coordinators, transport
adapters and framework bridges. For each one it gives the specification
version it targets, the package version, what it covers, how it is tested,
its status and its licence.

Brightbeam AI wrote every implementation listed here. An implementation by
another team is welcome: see [Wanted](#wanted).

> **Adding an entry**: open a pull request against this file with a row in
> the "Implementations" table and a short note under "Notes by
> implementation". Say how the implementation is tested. If you ran the
> conformance harness, link to its output or to the attestation it writes
> with `--attest` (see
> [`conformance/harness/README.md`](./conformance/harness/README.md)). The
> [`chap-conformance` Action](./.github/actions/chap-conformance/) needs
> fixing before it can be used: it passes a `--profiles` option that the
> harness rejects.

## Implementations

| Name | Language | Specification | Package | Surface | Tested with | Status | Licence | Authors |
|---|---|---|---|---|---|---|---|---|
| `@brightbeamai/chap-coordinator` | TypeScript | 0.2 | 0.2.13 | Core and every profile. Identity profiles as verifier hooks. Methods marked spec-only or reserved are not built. | Package tests and the differential fuzzer. No recorded harness run against this package. | Beta | Apache-2.0 | Brightbeam AI |
| `chap-coordinator` | Python 3.10+ | 0.2 | 0.2.13 | As the TypeScript coordinator, with MCP and A2A transports as modules. | Package tests and the differential fuzzer. Harness (Core and review) through `reference/python/server.py`, passing at v0.2.13. | Beta | Apache-2.0 | Brightbeam AI |
| `@brightbeamai/chap-coordinator-mcp` | TypeScript | 0.2 | 0.2.13 | Every method the coordinator implements, as MCP tools. | Package tests. No harness run over MCP. | Beta | Apache-2.0 | Brightbeam AI |
| `@brightbeamai/chap-coordinator-a2a` | TypeScript | 0.2 | 0.2.13 | Every method the coordinator implements, as skills on an A2A 0.3 Agent Card. | Package tests. No harness run over A2A. | Beta | Apache-2.0 | Brightbeam AI |
| `chap-langgraph` | Python 3.10+ | 0.2 | 0.2.13 | Records approve, reject and override decisions from LangGraph. | Bridge tests in CI | Beta | Apache-2.0 | Brightbeam AI |
| `chap-pydantic-ai` | Python 3.10+ | 0.2 | 0.2.13 | Records approve, reject and override decisions from Pydantic AI. | Bridge tests in CI | Beta | Apache-2.0 | Brightbeam AI |
| `chap-ag2` | Python 3.10+ | 0.2 | 0.2.13 | Records approve, reject and override decisions from AG2 (AutoGen). | Bridge tests in CI | Beta | Apache-2.0 | Brightbeam AI |
| `chap-llama-index` | Python 3.10+ | 0.2 | 0.2.13 | Records approve, reject and override decisions from LlamaIndex Workflows. | Bridge tests in CI | Beta | Apache-2.0 | Brightbeam AI |
| `chap-google-adk` | Python 3.10+ | 0.2 | 0.2.13 | Records approve, reject and override decisions from Google ADK. | Bridge tests in CI | Beta | Apache-2.0 | Brightbeam AI |

"Specification" is the CHAP version an entry implements, and "Package" is
the release of the package. Every package here is Beta: CHAP 0.2 is a draft,
and before 1.0 a minor release may break things
([ROADMAP.md, section "Version numbers"](./ROADMAP.md#version-numbers)).

The method catalogue,
[`schemas/profiles/chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json),
marks each method implemented, spec-only or reserved. Both coordinators
build every method marked implemented, and the MCP and A2A adapters expose
those methods.

The differential fuzzer,
[`conformance/differential/fuzz.py`](./conformance/differential/fuzz.py),
drives the Python coordinator with generated calls, replays them into the
TypeScript coordinator, and fails if a response or the chain head differs.
It covers Core, review, control, handoff, whisper, deliberation and routing.
CI runs it on every pull request.

## Notes by implementation

### `@brightbeamai/chap-coordinator` (TypeScript)

The protocol as a library. Embed it in a Node service, drive it directly
from a script, or put one of the transport adapters in front of it. It
covers Core and the review/1.0, modes/1.0, routing/1.0, whisper/1.0,
deliberation/1.0, handoff/1.0, control/1.0, security-signed/1.0 and
audit-scitt/1.0 profiles. For identity-oidc/1.0 and identity-vc/1.0 it binds
a participant's key at `participant.join` through verifier functions that
the deployment supplies. It has no runtime dependencies. `better-sqlite3` is
an optional dependency for persistent storage.

Package: [`packages/coordinator/`](./packages/coordinator/) ·
Testing: the package tests, which include the shared conformance vectors,
and the differential fuzzer. CI runs the conformance harness against
`reference/core-plus-review/server.ts`, a standalone server that does not
use this package, so no harness run against this package is recorded.

### `chap-coordinator` (Python)

A second implementation of the same surface, written by the same team. It
speaks the same JSON-RPC 2.0 messages as the TypeScript coordinator and
covers the same profiles. It includes the same wrap helpers, and MCP and A2A
server transports as Python modules. Its A2A transport declares A2A 1.0.

Package: [`packages/coordinator-py/`](./packages/coordinator-py/) ·
Testing: the package tests, the differential fuzzer, and the conformance
harness for Core and review, which CI runs against
`reference/python/server.py`, a server built on this package. The harness
passes at v0.2.13.

### `@brightbeamai/chap-coordinator-mcp` (MCP transport adapter)

Wraps a coordinator as an MCP server. Every method the coordinator
implements becomes an MCP tool named `chap.<method>`. It targets MCP
2026-07-28 and also serves 2025-11-25 clients. The adapter holds no state
and passes each call to the coordinator. Its tests drive a coordinator
through an MCP client and cover the tool list, annotations, argument
coercion and discovery. The conformance harness has not been run over MCP.

Package: [`packages/coordinator-mcp/`](./packages/coordinator-mcp/) ·
Walkthrough: [`examples/drive-chap-from-claude-desktop.md`](./examples/drive-chap-from-claude-desktop.md).

### `@brightbeamai/chap-coordinator-a2a` (A2A transport adapter)

Wraps a coordinator as an A2A agent. Every method the coordinator implements
becomes an `AgentSkill` on the Agent Card. The adapter declares A2A protocol
version 0.3.0 and uses the A2A JavaScript SDK 0.3. The A2A transport in the
Python coordinator declares A2A 1.0, so the two A2A surfaces differ. The
conformance harness has not been run over A2A.

Package: [`packages/coordinator-a2a/`](./packages/coordinator-a2a/) ·
Walkthrough: [`examples/drive-chap-from-an-a2a-orchestrator.md`](./examples/drive-chap-from-an-a2a-orchestrator.md).

### `chap-langgraph` (LangGraph bridge)

Connects LangGraph workflows to a CHAP coordinator. It turns LangGraph's
`interrupt()` and `Command(resume=...)` cycle into the CHAP `task.complete`,
`review.request` and `decide.*` sequence, so each human-in-the-loop
checkpoint is recorded on the workspace's audit log. By default the bridge
opens a workspace with `core/1.0` and `review/1.0`, and its entries are
hash-linked only when the coordinator has the chain switched on, for
example with `enable_chain=True`. LangGraph is an optional dependency.

Package: [`packages/chap-langgraph/`](./packages/chap-langgraph/) ·
Examples: [`packages/chap-langgraph/examples/`](./packages/chap-langgraph/examples/).

### `chap-pydantic-ai` (Pydantic AI bridge)

Bridges [Pydantic AI](https://ai.pydantic.dev)'s deferred-tool approval flow
(`ToolApproved` / `ToolDenied`) to CHAP. An approval becomes
`decide.approve`, an approval with edited arguments becomes
`decide.override` carrying the diff, and a denial becomes `decide.reject`.
Per-call rationale and tags are read from the tool-result metadata.
Pydantic AI is an optional dependency.

Package: [`packages/chap-pydantic-ai/`](./packages/chap-pydantic-ai/) ·
Examples: [`packages/chap-pydantic-ai/examples/`](./packages/chap-pydantic-ai/examples/).

### `chap-ag2` (AG2 / AutoGen bridge)

Bridges [AG2](https://github.com/ag2ai/ag2) (AutoGen) agent turns to CHAP,
recording the human's decision on a proposed turn as the matching
`decide.*` entry. AG2 is an optional dependency.

Package: [`packages/chap-ag2/`](./packages/chap-ag2/) ·
Examples: [`packages/chap-ag2/examples/`](./packages/chap-ag2/examples/).

### `chap-llama-index` (LlamaIndex Workflows bridge)

Bridges [LlamaIndex
Workflows](https://developers.llamaindex.ai/python/framework/understanding/workflows/)
human-in-the-loop events to CHAP's `review`/`decide` sequence.
LlamaIndex is an optional dependency.

Package: [`packages/chap-llama-index/`](./packages/chap-llama-index/) ·
Examples: [`packages/chap-llama-index/examples/`](./packages/chap-llama-index/examples/).

### `chap-google-adk` (Google ADK bridge)

Bridges [Google ADK](https://google.github.io/adk-docs/)
human-in-the-loop tool confirmations to CHAP. Approve, edit, and
reject map to `decide.approve` / `decide.override` / `decide.reject`.
Google ADK is an optional dependency.

Package: [`packages/chap-google-adk/`](./packages/chap-google-adk/) ·
Examples: [`packages/chap-google-adk/examples/`](./packages/chap-google-adk/examples/).

## Wanted

The list is open to any implementation of the CHAP wire format. Especially
welcome:

- A coordinator written by another team, in any language
- Rust and Go implementations
- Production deployments behind authenticated transports (mTLS,
  OIDC step-up, DPoP), with a published architecture write-up
- Domain-specific profile contributions, such as a healthcare profile or
  a financial-services profile
