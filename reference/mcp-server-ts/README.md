# CHAP MCP reference server (TypeScript)

A stdio MCP server that wraps a CHAP Coordinator. Point Claude
Desktop, Cursor, Claude Code, or any other MCP client at this and
drive a CHAP workspace from natural language.

Spec target: MCP 2025-11-25. CHAP 0.2.

## Install

```bash
npm install
```

## Run

```bash
npm start
```

You should see, on stderr:

```
CHAP MCP reference server starting on stdio.
Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.
```

The server then waits for MCP protocol messages on stdin and writes
responses to stdout. Kill it with Ctrl+C.

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

Every CHAP method, as an MCP tool named `chap.<method>`. The
Coordinator instance enables every profile out of the box (Core,
review, whisper, deliberation, handoff, control, routing,
audit-scitt). State is in-memory and lost on exit.

## Five-minute walkthrough

[`examples/drive-chap-from-claude-desktop.md`](../../examples/drive-chap-from-claude-desktop.md)

## Architecture

See the [`@brightbeamai/coordinator-mcp`](../../packages/coordinator-mcp/)
package, which this reference uses unchanged. For production
deployments, persist state via the Coordinator's `onAudit` listener
and apply auth at the transport layer (MCP's OAuth 2.1 model
attaches to Streamable HTTP, not stdio).

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
