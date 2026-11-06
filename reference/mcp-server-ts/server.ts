/**
 * CHAP MCP reference server (TypeScript, stdio).
 *
 * Wraps a CHAP Coordinator and exposes every CHAP method as an MCP tool
 * over stdio, with every call made as one participant: the agent named by
 * CHAP_PARTICIPANT, `agent:assistant@local` when it is unset. A call that
 * names another `from` is refused, so the assistant cannot act in a
 * person's name. A person's decisions belong in a desk: the published
 * `chap-mcp-server --url` forwards an assistant's calls to a desk server.
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
 * The coordinator runs in memory in this process. State is lost when the
 * process exits; the published server keeps it with --db.
 *
 * Spec target: MCP 2026-07-28, serving 2025-11-25 clients as well. CHAP 0.3.
 */

import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import { makeChapMcpServer } from "@brightbeamai/chap-coordinator-mcp";

const PROFILES = [
  "core/1.0", "review/1.0", "whisper/1.0",
  "deliberation/1.0", "handoff/1.0", "control/1.0",
  "routing/1.0", "modes/1.0", "audit-scitt/1.0",
];

const participant = process.env.CHAP_PARTICIPANT?.trim() || "agent:assistant@local";
if (participant.startsWith("human:")) {
  process.stderr.write(`CHAP_PARTICIPANT ${participant} names a person; this server acts for an assistant.\n`);
  process.exit(2);
}

const coord = new Coordinator({ defaultProfiles: PROFILES });

const server = makeChapMcpServer(coord, {
  name:    "chap",
  version: "0.3.4",
  participant,
});

// Log to stderr only; stdout is reserved for the MCP protocol stream.
process.stderr.write(`CHAP MCP reference server starting on stdio, acting as ${participant}.\n`);
process.stderr.write(`Profiles enabled: ${PROFILES.join(", ")}.\n`);

const transport = new StdioServerTransport();
await server.connect(transport);

// The server runs until stdio closes; nothing more to do here.
