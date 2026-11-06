# @brightbeamai/chap-coordinator-a2a

A2A server adapter for the CHAP Coordinator. Wraps a `Coordinator`
instance and exposes every CHAP method as an
[A2A](https://a2a-protocol.org) `AgentSkill` on the Agent Card, so
any A2A-aware orchestrator (Azure AI Foundry, Amazon Bedrock
AgentCore, Google ADK, custom multi-agent systems) can register the
coordinator by URL and delegate work to it.

Spec target: **A2A 1.0**, through `@a2a-js/sdk` 1.x, serving A2A 0.3
clients as well. CHAP 0.3.

## Install

```bash
npm install @brightbeamai/chap-coordinator-a2a @a2a-js/sdk express
```

Node 20+ required. `@a2a-js/sdk` 1.3 or later is a peer dependency, and
`express` is the SDK's peer dependency for its Express handlers. The
adapter's own dependencies are `@brightbeamai/chap-coordinator` and
`@brightbeamai/chap-coordinator-mcp`, for the shared schemas.

## Quick start

```typescript
import express from "express";
import { Coordinator } from "@brightbeamai/chap-coordinator";
import { makeChapAgentCard, makeChapAgentExecutor } from "@brightbeamai/chap-coordinator-a2a";
import { AGENT_CARD_PATH } from "@a2a-js/sdk";
import { DefaultRequestHandler, InMemoryTaskStore } from "@a2a-js/sdk/server";
import { agentCardHandler, jsonRpcHandler, UserBuilder } from "@a2a-js/sdk/server/express";

const coord = new Coordinator({
  defaultProfiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"],
});

const card = makeChapAgentCard({ baseUrl: "http://127.0.0.1:9090/" });
const handler = new DefaultRequestHandler(card, new InMemoryTaskStore(), makeChapAgentExecutor(coord));

const app = express();
app.use(`/${AGENT_CARD_PATH}`, agentCardHandler({ agentCardProvider: handler, legacyCompat: { enabled: true } }));
app.use("/", jsonRpcHandler({
  requestHandler: handler,
  userBuilder: UserBuilder.noAuthentication,
  legacyCompat: { enabled: true },
}));
app.listen(9090, "127.0.0.1");
```

A runnable reference server is at
[`reference/a2a-server-ts/`](../../reference/a2a-server-ts/).

## What gets exposed

All 39 CHAP methods become A2A skills named `chap.<method>`,
matching the MCP transport's tool naming so callers fluent in one
are fluent in the other.

A message carries the CHAP params in a data part, a part whose
`content` is JSON data with media type `application/json`, the type the
card names for every skill's input and output. The skill id is looked up in
`message.metadata.skill` first, then in `skill` on the first data part; the
params are that part's `params`, or the rest of the part when there are
none. The answer is an agent message with one data part.

## A2A 0.3 clients

The card lists the JSON-RPC endpoint twice: at protocol version 1.0, and at
0.3. With `legacyCompat: { enabled: true }` on the SDK's agent-card and
JSON-RPC handlers, as above, a client still on A2A 0.3 reads the card in its
0.3 shape and calls `message/send` with `kind`-tagged parts at the same
URL, and the SDK translates in both directions; a 1.0 client sends
`A2A-Version: 1.0` and calls `SendMessage`. The executor sees only 1.0
types. The Python counterpart serves the same two versions.

## Architecture

- **One Coordinator, one A2A agent.** Multi-workspace handled
  inside the Coordinator.
- **Stateless adapter.** Each `SendMessage` translates to a
  JSON-RPC envelope and dispatches through `coord.dispatch()`. A CHAP
  call takes effect before the executor returns, so there is never one
  running to cancel.
- **Auth deferred.** A2A's security schemes attach to the Agent
  Card and are enforced at the HTTP transport.

## Tests

```bash
npm test
```

The integration tests cover the Agent Card shape, dispatch via data
part, dispatch via metadata, error surfacing, the full
workflow including override, and the deliberation flow.
`round_trip.test.ts` serves the adapter with the SDK's server and drives
it with the SDK's 1.0 client, and with a 0.3 client's requests.

## See also

- The integration narrative: [`integrations/CHAP-with-A2A.md`](../../integrations/CHAP-with-A2A.md)
- The walkthrough: [`examples/drive-chap-from-an-a2a-orchestrator.md`](../../examples/drive-chap-from-an-a2a-orchestrator.md)
- The Python counterpart: [`chap_coordinator.transports.a2a_server`](../coordinator-py/chap_coordinator/transports/a2a_server.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
