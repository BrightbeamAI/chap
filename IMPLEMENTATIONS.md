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
| `@brightbeamai/chap-coordinator` | TypeScript | 0.3 | 0.3.5 | Core and every profile. Identity profiles as verifier hooks. Methods marked spec-only or reserved are not built. | Package tests and the differential fuzzer. No recorded harness run against this package. | Beta | Apache-2.0 | Brightbeam AI |
| `chap-coordinator` | Python 3.10+ | 0.3 | 0.3.5 | As the TypeScript coordinator, with MCP and A2A transports as modules, and the CHAP half the framework bridges share, `chap_coordinator.bridge`. | Package tests and the differential fuzzer. Harness (Core and review) through `reference/python/server.py`, passing at v0.3.0. | Beta | Apache-2.0 | Brightbeam AI |
| `@brightbeamai/chap-coordinator-mcp` | TypeScript | 0.3 | 0.3.5 | Every method the coordinator implements, as MCP tools. | Package tests. No harness run over MCP. | Beta | Apache-2.0 | Brightbeam AI |
| `@brightbeamai/chap-coordinator-a2a` | TypeScript | 0.3 | 0.3.5 | Every method the coordinator implements, as skills on an A2A 0.3 Agent Card. | Package tests. No harness run over A2A. | Beta | Apache-2.0 | Brightbeam AI |
| `chap-langgraph` | Python 3.10+ | 0.3 | 0.3.5 | Puts a LangGraph draft in front of a person as a review, and carries the graph on with their decision; records one made elsewhere, marked as recorded. | Bridge tests; with langgraph 1.2.20 through `scripts/test-bridges.sh` | Beta | Apache-2.0 | Brightbeam AI |
| `chap-pydantic-ai` | Python 3.10+ | 0.3 | 0.3.5 | Puts a Pydantic AI tool call that needs approval in front of a person, and carries out their decision; records one made elsewhere, marked as recorded. | Bridge tests; with pydantic-ai 2.54.0 through `scripts/test-bridges.sh` | Beta | Apache-2.0 | Brightbeam AI |
| `chap-ag2` | Python 3.10+ | 0.3 | 0.3.5 | Puts an AG2 (AutoGen) message, or a tool call, in front of a person, and carries out their decision; records one made elsewhere, marked as recorded. | Bridge tests; with ag2 0.14.0 and 1.2.2 through `scripts/test-bridges.sh` | Beta | Apache-2.0 | Brightbeam AI |
| `chap-llama-index` | Python 3.10+ | 0.3 | 0.3.5 | Puts a LlamaIndex Workflow's proposed output in front of a person, and answers the workflow with their decision; records one made elsewhere, marked as recorded. | Bridge tests; with llama-index-workflows 2.26.1 through `scripts/test-bridges.sh` | Beta | Apache-2.0 | Brightbeam AI |
| `chap-google-adk` | Python 3.10+ | 0.3 | 0.3.5 | Puts a Google ADK tool call that needs confirmation in front of a person, and resumes the run with their decision; records one made elsewhere, marked as recorded. | Bridge tests; with google-adk 2.11.0 through `scripts/test-bridges.sh` | Beta | Apache-2.0 | Brightbeam AI |

"Specification" is the CHAP version an entry implements, and "Package" is
the release of the package. Every package here is Beta: CHAP 0.3 is a draft,
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
passes at v0.3.0.

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

Connects LangGraph workflows to a CHAP coordinator. A node that makes a
draft opens its review on a task that requires one, `task.create` then
`task.complete`; the next node waits for the person's decision, made at a
CHAP desk, and the graph carries on with what CHAP holds as decided. A
decision made in the application's own interface, sent back as the
graph's resume value, is recorded in the reviewer's name as `decide.*`,
marked `via: "bridge-record"`, where nobody signs. By default the bridge
opens a workspace with `core/1.0` and `review/1.0`, and its entries are
hash-linked only when the coordinator has the chain switched on, for
example with `enable_chain=True`. LangGraph is an optional dependency.

Package: [`packages/chap-langgraph/`](./packages/chap-langgraph/) ·
Examples: [`packages/chap-langgraph/examples/`](./packages/chap-langgraph/examples/).

### `chap-pydantic-ai` (Pydantic AI bridge)

Bridges [Pydantic AI](https://ai.pydantic.dev)'s deferred-tool approval flow
(`ToolApproved` / `ToolDenied`) to CHAP. Each pending call is reviewed at a
CHAP desk, and the person's approval, edit of the arguments or denial comes
back as the `DeferredToolResults` that carry it out. A resolution the
application's own code made is recorded as `decide.approve`,
`decide.override` with the diff, or `decide.reject`, marked
`via: "bridge-record"`, where nobody signs. Pydantic AI is an optional
dependency.

Package: [`packages/chap-pydantic-ai/`](./packages/chap-pydantic-ai/) ·
Examples: [`packages/chap-pydantic-ai/examples/`](./packages/chap-pydantic-ai/examples/).

### `chap-ag2` (AG2 / AutoGen bridge)

Bridges [AG2](https://github.com/ag2ai/ag2) (AutoGen) to CHAP. At an AG2
0.x human-input turn, the agent's message, with the tool calls it would
make, is reviewed at a CHAP desk and the reply carries the decision out;
with AG2 1.x, a tool middleware puts each call in front of a person the
same way. A turn the application's own code took is recorded as the
matching `decide.*` entry, marked `via: "bridge-record"`, where nobody
signs. AG2 is an optional dependency.

Package: [`packages/chap-ag2/`](./packages/chap-ag2/) ·
Examples: [`packages/chap-ag2/examples/`](./packages/chap-ag2/examples/).

### `chap-llama-index` (LlamaIndex Workflows bridge)

Bridges [LlamaIndex
Workflows](https://developers.llamaindex.ai/python/framework/understanding/workflows/)
human-in-the-loop events to CHAP. The output an `InputRequiredEvent`
proposes is reviewed at a CHAP desk, and the `HumanResponseEvent` that
answers the pause carries the decision. A reply the application's own code
made is recorded as the matching `decide.*` entry, marked
`via: "bridge-record"`, where nobody signs. LlamaIndex is an optional
dependency.

Package: [`packages/chap-llama-index/`](./packages/chap-llama-index/) ·
Examples: [`packages/chap-llama-index/examples/`](./packages/chap-llama-index/examples/).

### `chap-google-adk` (Google ADK bridge)

Bridges [Google ADK](https://adk.dev/) human-in-the-loop tool
confirmations to CHAP. A paused call is reviewed at a CHAP desk, and the
`ToolConfirmation` that resumes the run carries the decision: confirmed,
confirmed with the person's arguments or their answer to the tool's own
question, or not confirmed. A confirmation the application's own code made
is recorded as `decide.approve`, `decide.override` or `decide.reject`,
marked `via: "bridge-record"`, where nobody signs, and only as ADK will
carry it out. Google ADK is an optional dependency.

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
