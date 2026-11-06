/**
 * CHAP A2A reference server (TypeScript, HTTP/JSON-RPC).
 *
 * Wraps a CHAP Coordinator and serves it as an A2A 1.0 agent over
 * Express. Other A2A-aware orchestrators can register this agent by
 * its base URL, discover capabilities at the agent-card endpoint,
 * and delegate work to it via JSON-RPC ``SendMessage``. A client still
 * on A2A 0.3 is answered too, ``message/send`` included, through the
 * SDK's compatibility layer.
 *
 * Usage:
 *
 *   tsx reference/a2a-server-ts/server.ts [--host 127.0.0.1] [--port 9090]
 *       [--as agent:orchestrator@local] [--db <path>] [--allowed-host <name:port>]...
 *
 * Every call is made as one participant, --as or CHAP_PARTICIPANT, an agent
 * by default; a human: URI is refused. The server answers only under its own
 * names (421 otherwise; --allowed-host or CHAP_ALLOWED_HOSTS adds one),
 * refuses a browser request from another origin (403), takes JSON only
 * (415) and caps the body (413). It has no authentication.
 *
 * Without --db (or CHAP_DB_PATH) the coordinator runs in memory, and state is
 * lost when the process exits.
 *
 * Spec target: A2A 1.0, serving 0.3 clients as well. CHAP 0.3.
 */

import { homedir } from "node:os";
import { resolve } from "node:path";
import { parseArgs } from "node:util";

import express, { type NextFunction, type Request, type Response } from "express";
import { AGENT_CARD_PATH } from "@a2a-js/sdk";
import {
  DefaultRequestHandler,
  InMemoryTaskStore,
} from "@a2a-js/sdk/server";
import { agentCardHandler, jsonRpcHandler, UserBuilder } from "@a2a-js/sdk/server/express";

import { Coordinator, type Store } from "@brightbeamai/chap-coordinator";
import {
  chapA2aGuard,
  makeChapAgentCard,
  makeChapAgentExecutor,
} from "@brightbeamai/chap-coordinator-a2a";

const USAGE = `Usage: tsx server.ts [options]

  --host <address>        the address to listen on (CHAP_HOST, default 127.0.0.1)
  --port <number>         the port (default 9090)
  --as <uri>              the participant every call is made as (CHAP_PARTICIPANT,
                          default agent:orchestrator@local); a human: URI is refused
  --db <path>             keep the workspaces in this SQLite file (CHAP_DB_PATH)
  --allowed-host <name>   another name the server answers to, as a Host header
                          carries it, such as a2a.internal:9090; repeatable
                          (CHAP_ALLOWED_HOSTS, comma separated)
  --help                  print this and exit
`;

function usageError(message: string): never {
  process.stderr.write(`${message}\nRun with --help for the options.\n`);
  process.exit(2);
}

let values: ReturnType<typeof parse>["values"];
function parse() {
  return parseArgs({
    options: {
      host: { type: "string" },
      port: { type: "string" },
      as: { type: "string" },
      db: { type: "string" },
      "allowed-host": { type: "string", multiple: true },
      help: { type: "boolean" },
    },
  });
}
try {
  ({ values } = parse());
} catch (err) {
  usageError(err instanceof Error ? err.message : String(err));
}
if (values.help) {
  process.stdout.write(USAGE);
  process.exit(0);
}

const port = Number(values.port ?? 9090);
if (!Number.isInteger(port) || port < 1 || port > 65535) usageError(`--port takes a port number; ${values.port} is not one`);
const host = values.host ?? (process.env.CHAP_HOST?.trim() || "127.0.0.1");

const participant = values.as ?? (process.env.CHAP_PARTICIPANT?.trim() || "agent:orchestrator@local");
if (participant.startsWith("human:")) {
  usageError(`${participant} names a person. This server acts for an agent, and a person decides in a desk.`);
}

const allowedHosts = [
  ...(values["allowed-host"] ?? []),
  ...(process.env.CHAP_ALLOWED_HOSTS ?? "").split(",").map((h) => h.trim()).filter(Boolean),
];
const loopback = ["127.0.0.1", "::1", "localhost"].includes(host);
const wildcard = host === "0.0.0.0" || host === "::";
// The address it listens on is one of its names, as in the create-chap-app servers.
if (!loopback && !wildcard) allowedHosts.push(`${host.includes(":") ? `[${host}]` : host}:${port}`);
const baseUrl = loopback || wildcard ? `http://localhost:${port}/` : `http://${host.includes(":") ? `[${host}]` : host}:${port}/`;

const PROFILES = [
  "core/1.0", "review/1.0", "whisper/1.0",
  "deliberation/1.0", "handoff/1.0", "control/1.0",
  "routing/1.0", "modes/1.0", "audit-scitt/1.0",
];

/** SqliteStore, loaded only when asked for: better-sqlite3 is an optional native dependency. */
async function openStore(path: string): Promise<Store> {
  const { SqliteStore } = await import("@brightbeamai/chap-coordinator/storage/sqlite");
  return new SqliteStore(path) as Store;
}

const rawDb = values.db ?? (process.env.CHAP_DB_PATH?.trim() || undefined);
const db = rawDb === undefined ? undefined : resolve(rawDb.replace(/^~(?=$|[\\/])/, homedir()));
const coord = new Coordinator({ defaultProfiles: PROFILES, ...(db ? { store: await openStore(db) } : {}) });
await coord.start();

const card = makeChapAgentCard({
  baseUrl,
  name: "CHAP Coordinator",
  version: "0.3.4",
});

const executor = makeChapAgentExecutor(coord, { participant });
const taskStore = new InMemoryTaskStore();

const handler = new DefaultRequestHandler(card, taskStore, executor);
const app = express();
// First, before any route: the Host, Origin, content-type and size checks.
app.use(chapA2aGuard({ allowedHosts }));
// legacyCompat serves a client on A2A 0.3 at the same endpoint: the card in
// its 0.3 shape when the request names no A2A-Version, and message/send.
app.use(`/${AGENT_CARD_PATH}`, agentCardHandler({ agentCardProvider: handler, legacyCompat: { enabled: true } }));
app.use("/", jsonRpcHandler({
  requestHandler: handler,
  userBuilder: UserBuilder.noAuthentication,
  legacyCompat: { enabled: true },
}));
app.use((_req: Request, res: Response) => { res.status(404).json({ error: "not found" }); });
// An error reaches the client as a short JSON body; its detail, a stack and
// file paths among it, stays in this process's log.
app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  console.error(err);
  if (!res.headersSent) res.status(500).json({ error: "internal error" });
});

process.stderr.write(`CHAP A2A reference server starting on ${baseUrl}, acting as ${participant}\n`);
process.stderr.write(`Agent Card: ${baseUrl}${AGENT_CARD_PATH}\n`);
process.stderr.write(`Profiles enabled: ${PROFILES.join(", ")}.\n`);
process.stderr.write(db ? `Persisting to ${db}\n` : "State is in memory only: the workspaces are lost when this process exits.\n");

if (!loopback) {
  process.stderr.write(
    `WARNING: binding to ${host} exposes an unauthenticated coordinator to ` +
    "the network. Use only on a trusted network.\n",
  );
}
app.listen(port, host);
