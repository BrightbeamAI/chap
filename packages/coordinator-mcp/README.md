# @brightbeamai/coordinator-mcp

MCP server adapter for the CHAP Coordinator. Wraps a `Coordinator`
instance and exposes every CHAP method as an [MCP](https://modelcontextprotocol.io)
tool, so any MCP client (Claude Desktop, Cursor, Claude Code, the
rest) can drive a CHAP workspace directly.

Spec target: **MCP 2025-11-25** · CHAP 0.2.

## Install

This package is distributed alongside the spec repo rather than
published to npm. To use it directly from source in another
TypeScript project:

```bash
# From the chap repo root
cd packages/coordinator-mcp
npm pack
# In your project
npm install /path/to/chap-coordinator-mcp-0.2.5.tgz
```

Node 18+ required. Runtime dependencies: `@brightbeamai/coordinator` and
`@modelcontextprotocol/sdk`.

## Quick start

```typescript
import { Coordinator } from "@brightbeamai/coordinator";
import { makeChapMcpServer } from "@brightbeamai/coordinator-mcp";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const coord = new Coordinator({
  defaultProfiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"],
});

const server = makeChapMcpServer(coord, { name: "chap", version: "0.2.5" });
await server.connect(new StdioServerTransport());
```

The returned `Server` is the official MCP SDK's `Server` class with
`tools/list` and `tools/call` handlers pre-registered. Attach any MCP
transport (stdio, Streamable HTTP, etc.).

A runnable reference server is at
[`reference/mcp-server-ts/`](../../reference/mcp-server-ts/).

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

8 integration tests use the SDK's `InMemoryTransport.createLinkedPair()`
to drive a wrapped Coordinator end-to-end, exercising every major
profile.

## See also

- The integration narrative: [`integrations/CHAP-with-MCP.md`](../../integrations/CHAP-with-MCP.md)
- The five-minute walkthrough: [`examples/drive-chap-from-claude-desktop.md`](../../examples/drive-chap-from-claude-desktop.md)
- The Python counterpart: [`chap_coordinator.transports.mcp_server`](../coordinator-py/chap_coordinator/transports/mcp_server.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
