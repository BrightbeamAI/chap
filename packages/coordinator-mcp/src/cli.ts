#!/usr/bin/env node
/**
 * CHAP MCP server (stdio).
 *
 * Runs a CHAP Coordinator and exposes every CHAP method as an MCP tool, so
 * any MCP client can drive a CHAP workspace.
 *
 *   npx @brightbeamai/chap-coordinator-mcp
 *
 * Client configuration (Claude Desktop, Claude Code, Cursor, and similar):
 *
 *   { "mcpServers": { "chap": {
 *       "command": "npx",
 *       "args": ["-y", "@brightbeamai/chap-coordinator-mcp"],
 *       "env": { "CHAP_DB_PATH": "~/chap.db" } } } }
 *
 * Environment
 *
 *   CHAP_DB_PATH   Path to a SQLite file. Without it the coordinator runs in
 *                  memory and everything is lost when the client restarts,
 *                  which is fine for a trial and wrong for real decisions.
 *   CHAP_PROFILES  Comma-separated profile list for new workspaces.
 *                  Defaults to the set below.
 *
 * To embed the server in your own process instead, import makeChapMcpServer
 * from this package and pass it a Coordinator you built yourself.
 *
 * Spec target: MCP 2026-07-28, serving 2025-11-25 clients as well.
 */
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { Coordinator } from "@brightbeamai/chap-coordinator";
import type { Store } from "@brightbeamai/chap-coordinator";
import { makeChapMcpServer } from "./index.js";

const VERSION = "0.2.11";

/**
 * Chaining is on by default because it costs almost nothing and the
 * alternative is worse: a workspace that adds audit-scitt/1.0 later can
 * never chain-verify the entries written before it, so verify_chain reports
 * not_evaluated for the rest of that workspace's life. Starting chained
 * avoids the gap entirely.
 */
const DEFAULT_PROFILES = [
  "core/1.0",
  "review/1.0",
  "whisper/1.0",
  "deliberation/1.0",
  "handoff/1.0",
  "control/1.0",
  "routing/1.0",
  "modes/1.0",
  "audit-scitt/1.0",
];

const profiles = process.env.CHAP_PROFILES
  ? process.env.CHAP_PROFILES.split(",").map(p => p.trim()).filter(Boolean)
  : DEFAULT_PROFILES;

if (profiles.length === 0) {
  process.stderr.write("CHAP_PROFILES was set but empty. Unset it to use the defaults.\n");
  process.exit(1);
}

/**
 * SqliteStore is loaded only when asked for. better-sqlite3 is a native
 * optional dependency, so it may be absent on a machine where the build did
 * not run. Failing loudly here beats starting in memory and silently
 * discarding the decisions the operator asked to persist.
 */
async function openStore(dbPath: string): Promise<Store> {
  try {
    const { SqliteStore } = await import("@brightbeamai/chap-coordinator/storage/sqlite");
    return new SqliteStore(dbPath) as Store;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    process.stderr.write(
      `CHAP_DB_PATH is set to ${dbPath} but the SQLite store could not be opened.\n` +
      `${detail}\n` +
      "better-sqlite3 is an optional native dependency; reinstall with build tools\n" +
      "available, or unset CHAP_DB_PATH to run in memory without persistence.\n",
    );
    process.exit(1);
  }
}

/**
 * Wrapped in a function rather than run at the top level: tsup emits both
 * ESM and CJS, and top-level await cannot compile to CJS.
 */
async function main(): Promise<void> {
  const dbPath = process.env.CHAP_DB_PATH?.trim();
  const store  = dbPath ? await openStore(dbPath) : undefined;

  const coord = new Coordinator({ defaultProfiles: profiles, ...(store ? { store } : {}) });
  // Loads any persisted workspaces before the first request is served.
  await coord.start();

  const server = makeChapMcpServer(coord, { name: "chap", version: VERSION });

  // stdout carries the MCP protocol stream; anything written there corrupts it.
  process.stderr.write(`CHAP MCP server ${VERSION} on stdio.\n`);
  process.stderr.write(`Profiles: ${profiles.join(", ")}\n`);
  process.stderr.write(
    dbPath
      ? `Persisting to ${dbPath}\n`
      : "In memory. Set CHAP_DB_PATH to keep workspaces across restarts.\n",
  );

  await server.connect(new StdioServerTransport());
}

main().catch((err: unknown) => {
  process.stderr.write(`CHAP MCP server failed to start: ${
    err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`);
  process.exit(1);
});
