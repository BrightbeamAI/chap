# @brightbeamai/chap-coordinator-mcp

MCP server adapter for the CHAP Coordinator. Wraps a `Coordinator`
instance and exposes every CHAP method as an [MCP](https://modelcontextprotocol.io)
tool, so any MCP client (Claude Desktop, Cursor, Claude Code, the
rest) can drive a CHAP workspace directly.

Spec target: **MCP 2026-07-28**, serving MCP 2025-11-25 clients as well · CHAP 0.3.

## Run it as a server

The package ships a stdio server, `chap-mcp-server`, so an MCP client can use
CHAP without any code. It makes every call as one participant, by default the
agent `agent:assistant@local`, and offers the tools an agent's own work needs.
A person decides in a desk, never through the assistant, so the server
refuses to act as a `human:` participant and refuses a call that names
anyone else.

The usual setup has a CHAP server with a desk, such as a project made with
`npx create-chap-app my-gate --template mcp-gate`, and points the stdio server
at that server's JSON-RPC endpoint. The assistant's work then waits for the
person in the desk:

```json
{
  "mcpServers": {
    "chap": {
      "command": "npx",
      "args": ["-y", "@brightbeamai/chap-coordinator-mcp",
               "--url", "http://127.0.0.1:8787/chap", "--as", "agent:assistant"]
    }
  }
}
```

| Option | Environment | Effect |
|--------|-------------|--------|
| `--as <uri>` | `CHAP_PARTICIPANT` | The participant every call is made as. Default `agent:assistant@local`. `from` is filled in, a call naming anyone else is refused, and a `human:` URI is refused at start. |
| `--tools agent\|all` | | `agent`, the default, offers `AGENT_MCP_TOOLS` (below). `all` offers all 39 methods, still as the one participant. |
| `--url <endpoint>` | | Forward every call to a running CHAP server's JSON-RPC endpoint, such as a desk server's `http://127.0.0.1:8787/chap`. That server keeps the workspaces, its store and its profiles. |
| `--db <path>` | `CHAP_DB_PATH` | Keep the workspaces in this SQLite file. A leading `~` is the home directory. |
| `--profiles <list>` | `CHAP_PROFILES` | Comma-separated profiles for new workspaces. Defaults to Core plus review, whisper, deliberation, handoff, control, routing, modes and audit-scitt, so the audit chain is on from the first entry. |
| `--help` | | Print the options and exit. |

An option takes its value as `--as agent:x` or `--as=agent:x`, and wins over
its environment variable; an empty variable counts as unset.

Without `--url` the server runs its own coordinator. With neither `--url` nor
`--db` it keeps everything in memory, says so on stderr, and every workspace
is lost when the client restarts. The agent tools create no workspace and
join no one, so a coordinator of its own suits a store that already holds the
workspace, or `--tools all`, where the assistant creates a workspace and
joins it as itself.

Persistence needs `better-sqlite3`, an optional native dependency of the
coordinator. If `--db` or `CHAP_DB_PATH` is set and it cannot be loaded the
server says so and exits, so it never starts in memory and discards the
decisions it was asked to keep.

The server is also listed in the [MCP registry](https://registry.modelcontextprotocol.io)
as `io.github.BrightbeamAI/chap`.

## Install

```bash
npm install @brightbeamai/chap-coordinator-mcp
```

Node 20+ required. Runtime dependencies: `@brightbeamai/chap-coordinator`,
`@modelcontextprotocol/sdk` and `zod`. The binary is `chap-mcp-server`.

## Quick start

```typescript
import { Coordinator } from "@brightbeamai/chap-coordinator";
import { AGENT_MCP_TOOLS, makeChapMcpServer } from "@brightbeamai/chap-coordinator-mcp";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const coord = new Coordinator({
  defaultProfiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"],
});

// The assistant's own connection: every call as the agent, with its tools.
const server = makeChapMcpServer(coord, {
  participant: "agent:assistant@local",
  toolFilter: (name) => AGENT_MCP_TOOLS.has(name),
});
await server.connect(new StdioServerTransport());
```

The returned `Server` is the official MCP SDK's `Server` class with
`tools/list` and `tools/call` handlers pre-registered. Attach any MCP
transport (stdio, Streamable HTTP, etc.).

`makeChapMcpServer(coord, options)` takes:

| Option | Effect |
|--------|--------|
| `participant` | Make every call as this participant: `from` is filled in, and a call that names another `from` is refused. The tools list `from` as optional, with this URI in its description. |
| `toolFilter` | `(toolName) => boolean`. A tool the filter excludes is left out of the list and refused when called by name. |
| `name`, `version` | What the server reports to its client. |
| `envelopeIdFactory` | The id of each envelope a tool call sends. |

Without `participant` or `toolFilter`,
`makeChapMcpServer(coord, { name: "chap", version: "0.3.4" })` serves all 39
tools and each call names its own `from`. That suits a connection that stands
for no one member, such as a service of your own that authenticates its
callers and checks the `from` they send.

`coord` is a `Coordinator`, or any object with the same
`dispatch(envelope)`, returning the response or a promise of it.

`AGENT_MCP_TOOLS` is the agent's set: `chap.task.create`, `chap.task.update`,
`chap.task.complete`, `chap.whisper.ask`, `chap.decide.approve`,
`chap.decide.reject`, `chap.decide.override`, `chap.workspace.describe`,
`chap.audit.read` and `chap.audit.verify_chain`. The decision tools stay for
work the agent is asked to review; on its own work the coordinator refuses
them with `-32011` and records the attempt. It leaves out the tools that
choose who reviews the agent's work or pass its review on:
`chap.review.request`, whose `to` can name the agent itself,
`chap.escalate.raise`, which ends the open review, and every workspace,
membership and control method. `chap.task.update` stays, so the agent can
take a task under review back to `in_progress`, which withdraws its draft.
Its next completion opens a new review where the task has
`review_required`, and completes the task where it does not, so set
`review_required` on any task the agent must not finish alone.

A runnable reference server is at
[`reference/mcp-server-ts/`](../../reference/mcp-server-ts/).

## Serve it over HTTP

`chapMcpHttpHandler` answers MCP on an HTTP endpoint such as `/mcp`, for a
Node `http` request and response, in both protocol eras:

```typescript
function chapMcpHttpHandler(
  coord: ChapDispatcher,
  options?: ChapMcpHttpOptions,
): (req: IncomingMessage, res: ServerResponse, parsedBody?: unknown) => Promise<void>;

interface ChapMcpHttpOptions extends ChapMcpOptions {
  maxBodyBytes?: number;   // default 1,114,112: 1 MiB, and 64 KiB of MCP around it
}
```

`options` are those of `makeChapMcpServer`, so `participant` and
`toolFilter` hold over HTTP as on stdio. Pass `parsedBody` when your server
has read and parsed the body already; otherwise the handler reads it, up to
`maxBodyBytes`.

```typescript
import { createServer } from "node:http";
import { AGENT_MCP_TOOLS, chapMcpHttpHandler } from "@brightbeamai/chap-coordinator-mcp";

const mcp = chapMcpHttpHandler(coord, {
  participant: "agent:assistant",
  toolFilter: (name) => AGENT_MCP_TOOLS.has(name),
});
createServer((req, res) => {
  // Refuse a foreign Host and Origin here, before any route.
  if (new URL(req.url ?? "/", "http://localhost").pathname === "/mcp") return mcp(req, res);
  res.writeHead(404).end();
}).listen(8787, "127.0.0.1");
```

A request with no `MCP-Protocol-Version` header, or a handshake-era version
(2025-11-25 and earlier), goes to the SDK's Streamable HTTP transport, with
a server and a transport for each request and no session. Any other version
takes the 2026-07-28 binding: one POST carrying one JSON-RPC request,
answered with one JSON object. Its headers are checked against its body, and
an error carries that binding's status: 400 for a malformed request, a
mismatched header (`-32020`) or an unsupported version (`-32022`), 404 for a
method it does not serve, 405 for anything but POST. The SDK's transport
alone refuses every 2026-07-28 request with 400.

The handler checks neither `Host` nor `Origin`. Mount it behind the checks
your server applies to every request, as the create-chap-app servers do: a
foreign `Host` refused with 421 against DNS rebinding, and a foreign
`Origin` with 403.

## What an unsigned local server trusts

MCP calls carry no signature. `participant` keeps a connection to one name,
and that is all it does: the stdio server trusts the process that started it,
so whatever the client sends is made as the participant. The log records
that the call came through this connection; it does not show that the
participant signed it. Anything that can start the command, or reach a
server's MCP endpoint, acts as that participant.

With `--url`, the server at the other end applies its own rules to each call.
One that requires a signature on every call refuses these with `-32070`; one
that requires signatures from people only accepts the agent's unsigned calls.
The pin covers this connection only. A CHAP server without signatures
accepts a call in any member's name from any local process, so the record of
a person's decision holds only where the server requires the desk's
signature on it.

## What gets exposed

All 39 CHAP methods become MCP tools named `chap.<method>`. Each
tool's `inputSchema` is the JSON Schema for the corresponding method's
params. Tool descriptions are tuned for LLM consumption.

The `chap.` prefix avoids collisions with other MCP servers a client
might have loaded simultaneously.

## Architecture

- **One Coordinator, one MCP server.** Multi-workspace is handled
  inside the Coordinator (workspaces are addressable by id); the MCP
  layer is stateless and routes every tool call through
  `coord.dispatch()`.
- **Single-sourced schemas.** Tool inputs are described by JSON
  Schemas (not Zod) and passed straight to the Coordinator without
  re-validation at the MCP layer. The Coordinator's own dispatch
  validates params and returns spec-correct JSON-RPC error codes,
  which the adapter surfaces as MCP tool errors.
- **Auth deferred.** The adapter ships unauthenticated; MCP's
  OAuth 2.1 auth model attaches at the Streamable HTTP transport,
  not inside the adapter.

## Tests

```bash
npm test
```

The integration tests use the SDK's `InMemoryTransport.createLinkedPair()`
to drive a wrapped Coordinator end-to-end, exercising every major
profile. `cli.test.ts` starts `chap-mcp-server` the way an MCP client does,
with and without `--url`, and `http.test.ts` serves both protocol eras over
HTTP.

## See also

- The integration narrative: [`integrations/CHAP-with-MCP.md`](../../integrations/CHAP-with-MCP.md)
- The five-minute walkthrough: [`examples/drive-chap-from-claude-desktop.md`](../../examples/drive-chap-from-claude-desktop.md)
- The Python counterpart: [`chap_coordinator.transports.mcp_server`](../coordinator-py/chap_coordinator/transports/mcp_server.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
