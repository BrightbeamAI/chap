/**
 * CHAP MCP reference server (TypeScript, stdio).
 *
 * Wraps a CHAP Coordinator and exposes every CHAP method as an MCP
 * tool over stdio. Point Claude Desktop, Cursor, or any other MCP
 * client at this binary to drive a CHAP workspace from natural
 * language.
 *
 * Usage:
 *
 *   tsx reference/mcp-server-ts/server.ts
 *
 * MCP client config (Claude Desktop, ~/Library/Application Support/Claude/claude_desktop_config.json):
 *
 *   {
 *     "mcpServers": {
 *       "chap": {
 *         "command": "tsx",
 *         "args": ["/absolute/path/to/chap/reference/mcp-server-ts/server.ts"]
 *       }
 *     }
 *   }
 *
 * The coordinator runs in-memory in this process. State is lost when
 * the process exits. For a persistent deployment, layer a state
 * store on top via `coord.onAudit(...)` and `coord.snapshot()` /
 * `coord.restore()`.
 *
 * Spec target: MCP 2025-11-25. CHAP 0.2.
 */

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { Coordinator } from "@chap/coordinator";
import { makeChapMcpServer } from "@chap/coordinator-mcp";

const coord = new Coordinator({
  defaultProfiles: [
    "core/1.0", "review/1.0", "whisper/1.0",
    "deliberation/1.0", "handoff/1.0", "control/1.0",
    "routing/1.0", "audit-scitt/1.0",
  ],
});

const server = makeChapMcpServer(coord, {
  name:    "chap",
  version: "0.2.3",
});

// Log to stderr only; stdout is reserved for the MCP protocol stream.
process.stderr.write("CHAP MCP reference server starting on stdio.\n");
process.stderr.write("Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.\n");

const transport = new StdioServerTransport();
await server.connect(transport);

// The server runs until stdio closes; nothing more to do here.
