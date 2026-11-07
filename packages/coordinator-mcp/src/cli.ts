#!/usr/bin/env node
/**
 * CHAP MCP server (stdio).
 *
 * Serves CHAP to one MCP client as one participant: every call is made as
 * that member, by default the agent `agent:assistant`, as the mcp-gate
 * template names its agent, with the tools an agent's own work needs. A
 * person decides in a desk, so this server never acts as a `human:`
 * participant.
 *
 *   npx @brightbeamai/chap-coordinator-mcp --url http://127.0.0.1:8792/chap --as agent:assistant
 *
 * With --url it forwards every call to a running CHAP server's JSON-RPC
 * endpoint, such as a desk server's POST /chap, so the assistant's work
 * waits for the person at that desk. Without it, it runs its own
 * coordinator, in memory or on a SQLite file.
 *
 * Client configuration (Claude Desktop, Claude Code, Cursor, and similar):
 *
 *   { "mcpServers": { "chap": {
 *       "command": "npx",
 *       "args": ["-y", "@brightbeamai/chap-coordinator-mcp",
 *                "--url", "http://127.0.0.1:8792/chap", "--as", "agent:assistant"] } } }
 *
 * The options are in USAGE below. CHAP_PARTICIPANT, CHAP_DB_PATH and
 * CHAP_PROFILES stand in for --as, --db and --profiles; an empty one counts
 * as unset.
 *
 * To embed the server in your own process instead, import makeChapMcpServer
 * from this package and pass it a Coordinator you built yourself.
 *
 * Spec target: MCP 2026-07-28, serving 2025-11-25 clients as well.
 */
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { Coordinator } from "@brightbeamai/chap-coordinator";
import type { Store } from "@brightbeamai/chap-coordinator";
import { makeChapMcpServer, AGENT_MCP_TOOLS, type ChapDispatcher } from "./index.js";
import { remoteDispatcher } from "./remote.js";

const VERSION = "0.3.4";

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

const DEFAULT_PARTICIPANT = "agent:assistant";

const USAGE = `Usage: chap-mcp-server [options]

Serves CHAP to one MCP client on stdio, making every call as one participant.

Options:
  --as <uri>          the participant every call is made as (CHAP_PARTICIPANT,
                      default ${DEFAULT_PARTICIPANT}). A human: URI is refused:
                      a person decides in a desk, never through the assistant
  --tools agent|all   agent, the default: the tools an agent's own work needs,
                      listed below; all: every CHAP method
  --url <endpoint>    forward every call to a running CHAP server's JSON-RPC
                      endpoint, such as http://127.0.0.1:8792/chap, which
                      keeps the workspaces
  --db <path>         keep the workspaces in this SQLite file (CHAP_DB_PATH);
                      a leading ~ is the home directory. Without --db or
                      --url, state is in memory only
  --profiles <list>   comma-separated profiles for new workspaces
                      (CHAP_PROFILES, default those listed below)
  --help              print this and exit

The agent tools:
${[...AGENT_MCP_TOOLS].sort().map((name) => `  ${name}`).join("\n")}

The default profiles:
${DEFAULT_PROFILES.map((profile) => `  ${profile}`).join("\n")}
`;

/** A participant URI: a scheme, a colon, then printable characters with no quote, backslash or space. */
const URI_RE = /^[a-z][a-z0-9+.-]*:[\x21\x23-\x5b\x5d-\x7e]+$/;

class UsageError extends Error {}

interface Options {
  help:        boolean;
  participant: string;
  tools:       "agent" | "all";
  url?:        string;
  db?:         string;
  profiles:    string[];
}

/** An environment variable's value, or undefined when it is unset or blank. */
function fromEnv(name: string): string | undefined {
  const value = process.env[name]?.trim();
  return value ? value : undefined;
}

/** `~` or `~/...` at the start of a path is the home directory, as a shell would read it. */
function expandHome(path: string): string {
  return /^~(?=$|[\\/])/.test(path) ? homedir() + path.slice(1) : path;
}

function parseArgs(argv: string[]): Options {
  const given: Record<string, string> = {};
  let help = false;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") { help = true; continue; }
    const m = /^(--as|--tools|--url|--db|--profiles)(?:=(.*))?$/.exec(arg);
    if (!m) throw new UsageError(arg.startsWith("-") ? `Unknown option ${arg}` : `Unexpected argument ${arg}`);
    const value = m[2] ?? argv[++i];
    if (value === undefined || value === "") throw new UsageError(`${m[1]} needs a value`);
    given[m[1]] = value;
  }

  const [participant, participantFrom] = given["--as"] !== undefined
    ? [given["--as"], "--as"]
    : fromEnv("CHAP_PARTICIPANT") !== undefined ? [fromEnv("CHAP_PARTICIPANT")!, "CHAP_PARTICIPANT"] : [DEFAULT_PARTICIPANT, "--as"];
  if (!URI_RE.test(participant)) {
    throw new UsageError(`${participantFrom} ${participant} is not a participant URI. Use the form agent:name@example.org.`);
  }
  if (participant.startsWith("human:")) {
    throw new UsageError(
      `${participantFrom} ${participant} names a person. This server acts for an assistant, and a person ` +
      "decides in a desk; give the assistant's own URI, such as agent:assistant.",
    );
  }

  const tools = given["--tools"] ?? "agent";
  if (tools !== "agent" && tools !== "all") throw new UsageError(`--tools takes agent or all; ${tools} is neither`);

  let url: string | undefined;
  if (given["--url"] !== undefined) {
    let parsed: URL | undefined;
    try { parsed = new URL(given["--url"]); } catch { parsed = undefined; }
    if (!parsed || (parsed.protocol !== "http:" && parsed.protocol !== "https:")) {
      throw new UsageError(`--url takes the http or https address of a CHAP JSON-RPC endpoint, such as http://127.0.0.1:8792/chap; ${given["--url"]} is not one`);
    }
    url = parsed.href;
  }

  const [rawDb, dbFrom] = given["--db"] !== undefined ? [given["--db"], "--db"] : [fromEnv("CHAP_DB_PATH"), "CHAP_DB_PATH"];
  const [rawProfiles, profilesFrom] = given["--profiles"] !== undefined
    ? [given["--profiles"], "--profiles"] : [fromEnv("CHAP_PROFILES"), "CHAP_PROFILES"];
  if (url && rawDb !== undefined) {
    throw new UsageError(`--url forwards to a server that keeps its own store, so ${dbFrom} does not apply. Set one or the other.`);
  }
  if (url && rawProfiles !== undefined) {
    throw new UsageError(`--url forwards to a server that sets its own profiles, so ${profilesFrom} does not apply. Set one or the other.`);
  }

  let profiles = DEFAULT_PROFILES;
  if (rawProfiles !== undefined) {
    profiles = rawProfiles.split(",").map((p) => p.trim()).filter(Boolean);
    if (profiles.length === 0) throw new UsageError(`${profilesFrom} names no profiles. Leave it unset for the defaults.`);
  }

  return {
    help, participant, tools, url, profiles,
    ...(rawDb !== undefined ? { db: resolve(expandHome(rawDb)) } : {}),
  };
}

/**
 * SqliteStore is loaded only when asked for. better-sqlite3 is a native
 * optional dependency, so it may be absent on a machine where the build did
 * not run. Failing loudly here beats starting in memory and silently
 * discarding the decisions the operator asked to persist.
 */
async function openStore(dbPath: string): Promise<Store> {
  try {
    // SQLite creates the file, and the directory it goes in is made here.
    mkdirSync(dirname(dbPath), { recursive: true });
    const { SqliteStore } = await import("@brightbeamai/chap-coordinator/storage/sqlite");
    return new SqliteStore(dbPath) as Store;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    process.stderr.write(
      `The SQLite store at ${dbPath} could not be opened.\n` +
      `${detail}\n` +
      "better-sqlite3 is an optional native dependency; reinstall with build tools\n" +
      "available, or leave --db and CHAP_DB_PATH unset to run in memory without persistence.\n",
    );
    process.exit(1);
  }
}

/**
 * Wrapped in a function, since tsup emits both ESM and CJS, and top-level
 * await cannot compile to CJS.
 */
async function main(): Promise<void> {
  let options: Options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (err) {
    if (!(err instanceof UsageError)) throw err;
    process.stderr.write(`${err.message}\nRun chap-mcp-server --help for the options.\n`);
    process.exit(2);
  }
  if (options.help) {
    process.stdout.write(USAGE);
    return;
  }

  // stdout carries the MCP protocol stream; anything written there corrupts it.
  const toolSet = options.tools === "agent" ? "the agent tools" : "every CHAP tool";
  process.stderr.write(`CHAP MCP server ${VERSION} on stdio, acting as ${options.participant} with ${toolSet}.\n`);

  let dispatcher: ChapDispatcher;
  if (options.url) {
    dispatcher = remoteDispatcher(options.url);
    process.stderr.write(`Forwarding every call to ${options.url}, whose coordinator keeps the workspaces.\n`);
  } else {
    const store = options.db ? await openStore(options.db) : undefined;
    const coord = new Coordinator({ defaultProfiles: options.profiles, ...(store ? { store } : {}) });
    // Loads any persisted workspaces before the first request is served.
    await coord.start();
    dispatcher = coord;
    process.stderr.write(`Profiles: ${options.profiles.join(", ")}\n`);
    process.stderr.write(
      options.db
        ? `Persisting to ${options.db}\n`
        : "State is in memory only: the workspaces are lost when this process exits. Set --db or CHAP_DB_PATH to keep them.\n",
    );
    if (options.tools === "agent" && coord.workspaces.size === 0) {
      process.stderr.write(
        "No workspace exists yet, and the agent tools cannot create one: run once with --tools all, " +
        "or use --url to reach a server that has one.\n",
      );
    }
  }

  const server = makeChapMcpServer(dispatcher, {
    name: "chap",
    version: VERSION,
    participant: options.participant,
    ...(options.tools === "agent" ? { toolFilter: (name: string) => AGENT_MCP_TOOLS.has(name) } : {}),
  });
  await server.connect(new StdioServerTransport());
}

main().catch((err: unknown) => {
  process.stderr.write(`CHAP MCP server failed to start: ${
    err instanceof Error ? (err.stack ?? err.message) : String(err)}\n`);
  process.exit(1);
});
