# @brightbeamai/coordinator-a2a

A2A server adapter for the CHAP Coordinator. Wraps a `Coordinator`
instance and exposes every CHAP method as an
[A2A](https://a2a-protocol.org) `AgentSkill` on the Agent Card, so
any A2A-aware orchestrator (Azure AI Foundry, Amazon Bedrock
AgentCore, Google ADK, custom multi-agent systems) can register the
coordinator by URL and delegate work to it.

Spec target: **A2A 0.3.0** (the version implemented by `@a2a-js/sdk`).
CHAP 0.2.

## Install

This package is distributed alongside the spec repo rather than
published to npm. To use it directly from source in another
TypeScript project:

```bash
# From the chap repo root
cd packages/coordinator-a2a
npm pack
# In your project
npm install /path/to/chap-coordinator-a2a-0.2.5.tgz
```

Node 18+ required. Runtime dependencies: `@brightbeamai/coordinator`,
`@brightbeamai/coordinator-mcp` (for the shared schemas), and `@a2a-js/sdk`.

## Quick start

```typescript
import express from "express";
import { Coordinator } from "@brightbeamai/coordinator";
import {
  makeChapAgentCard,
  makeChapAgentExecutor,
} from "@brightbeamai/coordinator-a2a";
import {
  DefaultRequestHandler,
  InMemoryTaskStore,
} from "@a2a-js/sdk/server";
import { A2AExpressApp } from "@a2a-js/sdk/server/express";

const coord = new Coordinator({
  defaultProfiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"],
});

const card = makeChapAgentCard({ baseUrl: "http://localhost:9090" });
const executor = makeChapAgentExecutor(coord);

const handler = new DefaultRequestHandler(card, new InMemoryTaskStore(), executor);
const app = new A2AExpressApp(handler).setupRoutes(express(), "");
app.listen(9090);
```

A runnable reference server is at
[`reference/a2a-server-ts/`](../../reference/a2a-server-ts/).

## What gets exposed

All 39 CHAP methods become A2A skills named `chap.<method>`,
matching the MCP transport's tool naming so callers fluent in one
are fluent in the other.

A2A messages carry the CHAP params in a `DataPart`. The skill id
identifies which CHAP method to dispatch, looked up in this order:
`message.metadata.skill` first, then `part.data.skill` on the first
data part.

## Spec version asymmetry

The two official A2A SDKs are at different points of the spec
evolution. This adapter targets **A2A 0.3.0** via `@a2a-js/sdk`. The
Python counterpart in `chap_coordinator.transports.a2a_server`
targets **A2A 1.0** via `a2a-sdk`. The CHAP adapter layer is
identical across both; the spec-version difference is a property of
the SDK ecosystem we depend on. Both Agent Cards advertise the
correct version for each implementation.

## Architecture

- **One Coordinator, one A2A agent.** Multi-workspace handled
  inside the Coordinator.
- **Stateless adapter.** Each `message/send` translates to a
  JSON-RPC envelope and dispatches through `coord.dispatch()`.
- **Auth deferred.** A2A's security schemes attach to the Agent
  Card and are enforced at the HTTP transport.

## Tests

```bash
npm test
```

14 integration tests cover the Agent Card shape, dispatch via data
part, dispatch via metadata, error surfacing, the full
workflow including override, deliberation flow, and cancellation.

## See also

- The integration narrative: [`integrations/CHAP-with-A2A.md`](../../integrations/CHAP-with-A2A.md)
- The walkthrough: [`examples/drive-chap-from-an-a2a-orchestrator.md`](../../examples/drive-chap-from-an-a2a-orchestrator.md)
- The Python counterpart: [`chap_coordinator.transports.a2a_server`](../coordinator-py/chap_coordinator/transports/a2a_server.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
