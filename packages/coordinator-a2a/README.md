# @brightbeamai/chap-coordinator-a2a

A2A server adapter for the CHAP Coordinator. Wraps a `Coordinator`
instance and exposes CHAP methods as [A2A](https://a2a-protocol.org)
`AgentSkill`s on the Agent Card, an agent's methods by default and every
method where the server asks for them, so any A2A-aware orchestrator
(Azure AI Foundry, Amazon Bedrock AgentCore, Google ADK, custom
multi-agent systems) can register the coordinator by URL and delegate work
to it.

Spec target: **A2A 1.0**, through `@a2a-js/sdk` 1.x, serving A2A 0.3
clients as well. CHAP 0.3.

## Install

```bash
npm install @brightbeamai/chap-coordinator-a2a @brightbeamai/chap-coordinator @a2a-js/sdk express
```

Node 22+ required. The adapter's peer dependencies are `@a2a-js/sdk` 1.3
or later, `@brightbeamai/chap-coordinator`, and
`@brightbeamai/chap-coordinator-mcp` for the shared schemas; npm 7 and
later installs the last itself. `express` is the SDK's peer dependency for
its Express handlers.

## Quick start

```typescript
import express from "express";
import { Coordinator } from "@brightbeamai/chap-coordinator";
import { ChapTaskStore, chapA2aGuard, makeChapAgentCard, makeChapAgentExecutor } from "@brightbeamai/chap-coordinator-a2a";
import { AGENT_CARD_PATH } from "@a2a-js/sdk";
import { DefaultRequestHandler } from "@a2a-js/sdk/server";
import { agentCardHandler, jsonRpcHandler, UserBuilder } from "@a2a-js/sdk/server/express";

const coord = new Coordinator({
  defaultProfiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"],
});

const card = makeChapAgentCard({ baseUrl: "http://127.0.0.1:9090/" });
const executor = makeChapAgentExecutor(coord, { participant: "agent:orchestrator@local" });
const handler = new DefaultRequestHandler(card, new ChapTaskStore(), executor);

const app = express();
app.use(chapA2aGuard());   // first: Host, Origin, JSON only, and a size limit
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

`ChapTaskStore` is the task store to give the request handler. The executor
answers a call with a message and makes a task only to refuse one, a task
final the moment it is made. The SDK's `InMemoryTaskStore` would keep every
such task for as long as the server runs, with the request it refused in
its history. `ChapTaskStore` keeps the 1,000 most recent (`maxTasks` sets
another number), each without the client's own messages, so GetTask still
answers for a recent refusal and memory stays bounded.

## What a local server checks

A browser sends a request to a loopback address from any page the user
visits, so an A2A server on the user's machine checks every request before
its routes. `chapA2aGuard(options)` makes the create-chap-app servers'
checks, as Connect-style middleware for Express:

- a `Host` that is not one of the server's names is refused with 421, so a
  page that rebinds its own name to 127.0.0.1 gets nothing. The loopback
  names on the server's own port are allowed, and `allowedHosts` adds
  others, each as the `Host` header carries it, such as `a2a.internal:9090`;
- an `Origin` naming another site, or `null`, is refused with 403. A client
  that is not a browser sends none, and is let through;
- a POST must be `application/json` (415), which a page cannot send across
  origins without a preflight;
- a body over `maxBodyBytes`, 1,114,112 bytes by default, is refused with
  413 before the rest is read, one that is not JSON with 400, and one that
  nests deeper than 32 levels with 400, before a parser deeper in runs out
  of stack. A message holds the call's params six levels down, which leaves
  them 26, more than any CHAP call uses.

Every refusal is a short JSON body. The guard reads and parses the body, so
the SDK's JSON-RPC route finds it parsed and its own 100 kB parser never
runs. Add a final error handler that answers with JSON, as the reference
server does, so no error reaches a client as Express's HTML page.

The guard does not authenticate. Any local process can call the server, as
whichever participant the executor acts as; with `participant` set, as that
one only. A call carries no signature, so the log shows the call came
through this server and does not show who sent it. A2A's security schemes,
declared on the card and enforced at the transport, are how a deployment
names its callers.

## What gets exposed

The skills are CHAP methods, named `chap.<method>`, matching the MCP
transport's tool naming so callers fluent in one are fluent in the other.
By default the card lists, and the executor runs, the agent's skills: the
`AGENT_MCP_TOOLS` set from `@brightbeamai/chap-coordinator-mcp`, which
covers an agent's own work and leaves out workspace, membership and
control methods and the choice of who reviews. Pass `skillFilter: () =>
true` to both for every method.

A message carries the CHAP params in a data part, a part whose
`content` is JSON data with media type `application/json`, the type the
card names for every skill's input and output. The skill id is looked up in
`message.metadata.skill` first, then in `skill` on the first data part; the
params are that part's `params`, or the rest of the part when there are
none.

## Answers and refusals

An answer is an agent message with one data part, the call's result. A call
CHAP refuses is a failure in A2A's terms: a task the agent rejected
(`TASK_STATE_REJECTED`), or one that failed (`TASK_STATE_FAILED`) when the
coordinator could not complete the call (`-32603`). The task's status
message carries the CHAP error as a data part, with the error's `data` when
it has any:

```json
{ "chap_error": -32011, "message": "Not an addressed reviewer for this task: agent:bot" }
```

so a client that reads only A2A states sees the failure, and one that reads
CHAP codes has them. A 0.3 client sees the same task, in state `rejected` or
`failed`. A fault inside the server is reported without its text, which goes
to the server's log.

## One participant's connection

`makeChapAgentExecutor(coord, { participant })` makes every call as that
member, as `participant` does for the MCP server: `from` is filled in, and a
call that names anyone else is refused with `-32011`. Use it where the A2A
server is one participant's connection, such as an orchestrator's, so the
connection cannot act in anyone else's name, a person's included. Without
it, each call names its own `from`.

`skillFilter` names the skills: pass the same filter to `makeChapAgentCard`,
which lists the skills, and to the executor, which runs them and refuses an
excluded one with `-32601`. Unset, both take the agent's skills, so a
connection made for an orchestrator cannot pause the workspace, change its
members or profiles, or revoke a person's key. A server that also sets up
workspaces opts in to every method:

```typescript
const everySkill = () => true;
const card = makeChapAgentCard({ baseUrl: "http://127.0.0.1:9090/", skillFilter: everySkill });
const executor = makeChapAgentExecutor(coord, {
  participant: "agent:orchestrator@local",
  skillFilter: everySkill,
});
```

## A2A 0.3 clients

The card lists the JSON-RPC endpoint twice: at protocol version 1.0, and at
0.3. With `legacyCompat: { enabled: true }` on the SDK's agent-card and
JSON-RPC handlers, as above, a client still on A2A 0.3 reads the card in its
0.3 shape and calls `message/send` with `kind`-tagged parts at the same
URL, and the SDK translates in both directions; a 1.0 client sends
`A2A-Version: 1.0` and calls `SendMessage`. The executor sees only 1.0
types. The Python counterpart serves the same two versions. A server whose
handlers leave `legacyCompat` off passes `legacyCompat: false` to
`makeChapAgentCard` too, so the card lists only the 1.0 interface it
answers.

## Upgrading from 0.3.4

0.3.4 was built on `@a2a-js/sdk` 0.3. 0.3.5 is built on the 1.x SDK, A2A
1.0, and keeps the 0.3.4 calls working:

- **The SDK.** The peer dependency is `@a2a-js/sdk` `^1.3`, where it was
  `^0.3.13`: install `@a2a-js/sdk@^1.3` with the adapter.
- **Your server.** The 1.x SDK has no `A2AExpressApp`. Mount its
  `agentCardHandler` and `jsonRpcHandler` as the quick start does, with
  `legacyCompat: { enabled: true }`, so a client still on A2A 0.3 is
  answered at the same URL, and give the request handler a `ChapTaskStore`.
- **The skills.** The card and the executor offer the agent's skills unless
  `skillFilter` names others; 0.3.4 offered every method. Pass
  `skillFilter: () => true` to both to keep that.
- **Refusals** are tasks, `TASK_STATE_REJECTED` or `TASK_STATE_FAILED`, with
  the CHAP error in the status message; 0.3.4 answered with a message
  carrying `chap_error`. A 0.3 client sees the states `rejected` and
  `failed`.
- **`dispatchA2aMessage`** takes options, `{ envelopeId, skillFilter,
  participant }`. `dispatchA2aMessage(coord, message, envelopeId)` works as
  it did, and so does a message in the 0.3 SDK's shape, its parts tagged by
  `kind`.

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
it with the SDK's 1.0 client, and with a 0.3 client's requests;
`participant_and_refusals.test.ts`, `skill_filter.test.ts`,
`task_store.test.ts` and `guard.test.ts` cover the participant pin,
refusals as rejected tasks, the skill set, the task store and the guard's
checks.

## See also

- The integration narrative: [`integrations/CHAP-with-A2A.md`](../../integrations/CHAP-with-A2A.md)
- The walkthrough: [`examples/drive-chap-from-an-a2a-orchestrator.md`](../../examples/drive-chap-from-an-a2a-orchestrator.md)
- The Python counterpart: [`chap_coordinator.transports.a2a_server`](../coordinator-py/chap_coordinator/transports/a2a_server.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
