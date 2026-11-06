# CHAP MCP reference server (TypeScript)

A stdio MCP server that wraps a CHAP Coordinator, as an example of embedding
[`@brightbeamai/chap-coordinator-mcp`](../../packages/coordinator-mcp/).
Every call is made as one participant, an agent, so an MCP client can drive a
workspace without being able to act in anyone else's name.

For use with a desk, where a person decides on the assistant's work, run the
published server instead: `npx @brightbeamai/chap-coordinator-mcp --url
http://127.0.0.1:8787/chap` forwards the assistant's calls to a desk server.
See [`examples/drive-chap-from-claude-desktop.md`](../../examples/drive-chap-from-claude-desktop.md).

Spec target: MCP 2026-07-28, serving 2025-11-25 clients as well. CHAP 0.3.

## Install

From the repository root, install the workspaces and build the packages this
server imports:

```bash
npm install
npm run build
```

Then run it from this directory.

## Run

```bash
npm start
```

You should see, on stderr:

```
CHAP MCP reference server starting on stdio, acting as agent:assistant@local.
Profiles enabled: core/1.0, review/1.0, whisper/1.0, deliberation/1.0, handoff/1.0, control/1.0, routing/1.0, modes/1.0, audit-scitt/1.0.
```

The server then waits for MCP protocol messages on stdin and writes
responses to stdout. Kill it with Ctrl+C.

`CHAP_PARTICIPANT` names the participant every call is made as; unset, it is
`agent:assistant@local`. A `human:` URI is refused at start: a person decides
in a desk, never through the assistant.

## Wire into Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent on your OS. Add the `chap` entry under
`mcpServers`:

```json
{
  "mcpServers": {
    "chap": {
      "command": "tsx",
      "args": ["/absolute/path/to/chap/reference/mcp-server-ts/server.ts"]
    }
  }
}
```

Restart Claude Desktop. "chap" should appear as a connected MCP
server with 39 tools.

## What it exposes

Every CHAP method, as an MCP tool named `chap.<method>`, made as the one
participant: `from` is filled in, and a call naming anyone else is refused.
The assistant can create a workspace, join it as itself and record its own
work; nothing here can decide as a person. New workspaces get the profiles
listed above. State is in memory and lost on exit.

## Architecture

See the [`@brightbeamai/chap-coordinator-mcp`](../../packages/coordinator-mcp/)
package, which this reference uses unchanged. For production
deployments, persist state with the Coordinator's `store` option
and apply auth at the transport layer: MCP's OAuth 2.1 model
attaches to Streamable HTTP, and stdio has none.

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
