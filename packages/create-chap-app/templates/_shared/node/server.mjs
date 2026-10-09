// The process that owns the store.
//
// It runs the CHAP coordinator on SQLite and serves four things:
//
//   GET  /              the review desk
//   POST /chap          CHAP calls as JSON-RPC, for any agent or client
//   GET  /api/...       what the desk needs that the protocol does not carry
//   POST /mcp           an MCP server over streamable HTTP, when enabled
//
// Everything comes from chap.config.json next to this file, with a few
// environment overrides noted below. Run it with `node server.mjs`.

import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Coordinator, MemoryStore } from "@brightbeamai/chap-coordinator";

const here = dirname(fileURLToPath(import.meta.url));

export async function loadConfig(path = join(here, "chap.config.json")) {
  const config = JSON.parse(await readFile(path, "utf8"));
  config.port = Number(process.env.PORT ?? config.port ?? 8787);
  config.host = process.env.CHAP_HOST ?? config.host ?? "127.0.0.1";
  config.store = process.env.CHAP_DB_PATH ?? config.store ?? "./data/chap.db";
  config.require_signatures = envFlag("CHAP_REQUIRE_SIGNATURES", config.require_signatures ?? false);
  // The coordinator turns the chain on for a workspace that advertises
  // audit-scitt/1.0 whatever the flag says, so the flag reports that too.
  config.chain = envFlag("CHAP_CHAIN", !!config.chain) || config.profiles.includes("audit-scitt/1.0");
  config.mcp = envFlag("CHAP_MCP", config.mcp ?? false);
  config.oidc = oidcFromEnv(config.oidc);
  return config;
}

function envFlag(name, fallback) {
  const v = process.env[name];
  if (v === undefined || v === "") return fallback;
  return v === "1" || v.toLowerCase() === "true";
}

function oidcFromEnv(configured) {
  const issuer = process.env.OIDC_ISSUER ?? configured?.issuer;
  if (!issuer) return null;
  return {
    issuer,
    jwks_url: process.env.OIDC_JWKS_URL ?? configured?.jwks_url ?? issuer.replace(/\/$/, "") + "/.well-known/jwks.json",
    audience: process.env.OIDC_AUDIENCE ?? configured?.audience ?? null,
  };
}

/**
 * The store. A path opens SQLite; ":memory:" keeps everything in the process,
 * which the tests use. better-sqlite3 is a native optional dependency of the
 * coordinator, so a machine where it did not build is told so here, with
 * the way to run without it, and nothing is silently kept in memory.
 */
export async function openStore(storePath) {
  if (storePath === ":memory:") return new MemoryStore();
  await mkdir(dirname(storePath), { recursive: true });
  try {
    const { SqliteStore } = await import("@brightbeamai/chap-coordinator/storage/sqlite");
    return new SqliteStore(storePath);
  } catch (err) {
    throw new Error(`The SQLite store at ${storePath} could not be opened: ${err instanceof Error ? err.message : String(err)}\n` +
      "Reinstall with build tools available so better-sqlite3 builds, or set CHAP_DB_PATH=:memory: to run without persistence.");
  }
}

/**
 * identity-oidc/1.0 is enforced by a token verifier, and the coordinator
 * adds the profile where a verifier is set and refuses a workspace that
 * advertises it without one (SPECIFICATION 15.1, item 3). The list here is
 * made to agree before the workspace is created, so the console, /api/config
 * and the descriptor say the same thing, and the console says what changed.
 */
export function reconcileOidcProfile(config, enforced) {
  const isOidc = (p) => p === "identity-oidc" || p.startsWith("identity-oidc/");
  const advertised = config.profiles.some(isOidc);
  if (enforced && !advertised) {
    config.profiles = [...config.profiles, "identity-oidc/1.0"];
    console.log("identity-oidc/1.0 added to the profiles: an OIDC issuer is configured, so tokens are verified at participant.join.");
  } else if (advertised && !enforced) {
    config.profiles = config.profiles.filter((p) => !isOidc(p));
    console.log("identity-oidc/1.0 left out of the profiles: no OIDC issuer is configured. Set OIDC_ISSUER to verify tokens at participant.join.");
  }
}

/** Build the coordinator from a config. Exported so tests can run in-process. */
export async function makeCoordinator(config, extra = {}) {
  reconcileOidcProfile(config, !!(config.oidc || extra.verifyOidcToken));
  const options = {
    store: await openStore(config.store),
    defaultProfiles: config.profiles,
    enableChain: !!config.chain,
    requireSignatures: !!config.require_signatures,
    ...extra,
  };
  if (config.oidc && !options.verifyOidcToken) {
    const { makeOidcVerifier } = await import("./lib/oidc.mjs");
    options.verifyOidcToken = await makeOidcVerifier(config.oidc);
  }
  const coord = new Coordinator(options);
  await coord.start();
  await bootstrap(coord, config);
  return coord;
}

/**
 * Create the workspace and join the configured participants.
 *
 * With signatures required, nobody is joined here: each participant joins
 * itself with its own key, since a re-join cannot add a key for someone.
 * The desk does this for a human; agent.mjs does it for the agent.
 */
export async function bootstrap(coord, config) {
  const send = (method, params) => coord.dispatch({ jsonrpc: "2.0", id: `boot-${method}`, method, params: { workspace: config.workspace, ...params } });
  if (!coord.getWorkspace(config.workspace)) {
    const r = send("workspace.create", { profiles: config.profiles, ...(config.mode ? { mode: config.mode } : {}), ...(config.mode_ceiling ? { mode_ceiling: config.mode_ceiling } : {}) });
    if (r.error) throw new Error(`workspace.create: ${r.error.message}`);
  }
  if (config.require_signatures) return;
  const ws = coord.getWorkspace(config.workspace);
  const members = [...(config.humans ?? []).map((h) => ({ ...h, type: "human" })), ...(config.agent ? [{ ...config.agent, type: "agent" }] : [])];
  for (const m of members) {
    if (ws.members.has(m.uri)) continue;
    const r = send("participant.join", { from: m.uri, type: m.type, role: m.role ?? (m.type === "human" ? "reviewer" : "drafter"), display_name: m.display_name });
    if (r.error) throw new Error(`participant.join ${m.uri}: ${r.error.message}`);
  }
}

/** The open reviews a reviewer can act on, read from the owning process. */
export function openReviews(coord, workspace, reviewer) {
  const ws = coord.getWorkspace(workspace);
  if (!ws) return [];
  const out = [];
  for (const task of ws.tasks.values()) {
    if (task.state !== "review_requested" || !task.review) continue;
    if (reviewer && !task.review.requested_to.includes(reviewer)) continue;
    out.push({
      task_id: task.id,
      kind: task.kind,
      state: task.state,
      assignee: task.assignee,
      input: task.input,
      artefact: task.pending_artefact ?? task.output ?? null,
      reviewers: task.review.requested_to,
      rule: task.review.rule,
      requested_at: task.review.requested_at,
      decisions: task.review.decisions,
    });
  }
  return out.sort((a, b) => a.requested_at.localeCompare(b.requested_at));
}

/** One task, for an agent waiting on a decision. */
export function taskView(coord, workspace, taskId) {
  const task = coord.getWorkspace(workspace)?.tasks.get(taskId);
  if (!task) return null;
  return {
    task_id: task.id, kind: task.kind, state: task.state, assignee: task.assignee,
    output: task.output ?? null, review: task.review ?? null, history: task.history,
  };
}

export function publicConfig(config) {
  return {
    workspace: config.workspace,
    profiles: config.profiles,
    humans: (config.humans ?? []).map(({ uri, display_name, role }) => ({ uri, display_name, role })),
    agent: config.agent ?? null,
    require_signatures: !!config.require_signatures,
    chain_enabled: !!config.chain,
    oidc: config.oidc ? { issuer: config.oidc.issuer } : null,
    mcp: !!config.mcp,
  };
}

// -- HTTP ---------------------------------------------------------------------

function reply(res, status, body, type = "application/json") {
  res.writeHead(status, { "content-type": type });
  res.end(type === "application/json" ? JSON.stringify(body) : body);
}

/**
 * A browser request from another origin. The desk is served from this
 * process, so its requests carry this server's own origin or none; a page
 * on another origin gets no cross-origin headers and its calls are refused,
 * so an open tab elsewhere cannot decide as the reviewer. A non-browser
 * client sends no Origin header.
 */
function foreignOrigin(req) {
  const origin = req.headers.origin;
  if (!origin) return false;
  return origin.toLowerCase() !== `http://${req.headers.host ?? ""}`.toLowerCase();
}

/**
 * A request whose Host header is not one of this server's own names. A page
 * that resolves its own name to this address (DNS rebinding) arrives with
 * that name as the Host, and is refused before any route.
 */
function foreignHost(req, config) {
  const host = (req.headers.host ?? "").toLowerCase();
  const port = req.socket.localPort;
  const allowed = new Set([`127.0.0.1:${port}`, `localhost:${port}`, `[::1]:${port}`, `${config.host}:${port}`.toLowerCase()]);
  if (config.allowed_hosts) for (const h of config.allowed_hosts) allowed.add(h.toLowerCase());
  return !allowed.has(host);
}

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

export async function makeServer(config, coord) {
  const desk = await readFile(join(here, "desk", "desk.html"), "utf8");
  const clientModule = await readFile(join(here, "desk", "chap-client.mjs"), "utf8");
  let mcp = null;
  if (config.mcp) {
    const { makeChapMcpServer } = await import("@brightbeamai/chap-coordinator-mcp");
    const { StreamableHTTPServerTransport } = await import("@modelcontextprotocol/sdk/server/streamableHttp.js");
    // Stateless: one server and transport per request, over the shared
    // coordinator. The MCP client keeps no session with this process.
    mcp = async (req, res, body) => {
      const server = makeChapMcpServer(coord, { name: config.workspace, version: "0.3.0" });
      const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
      res.on("close", () => { transport.close(); server.close(); });
      await server.connect(transport);
      await transport.handleRequest(req, res, body);
    };
  }

  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    try {
      if (foreignHost(req, config)) return reply(res, 421, { error: "unknown host; set allowed_hosts in chap.config.json to serve under another name" });
      if (foreignOrigin(req)) return reply(res, 403, { error: "cross-origin requests are refused" });
      if (url.pathname === "/" || url.pathname === "/desk" || url.pathname === "/desk.html") return reply(res, 200, desk, "text/html; charset=utf-8");
      if (url.pathname === "/chap-client.mjs") return reply(res, 200, clientModule, "text/javascript; charset=utf-8");
      if (url.pathname === "/chap" && req.method === "POST") {
        if (!(req.headers["content-type"] ?? "").toLowerCase().startsWith("application/json")) return reply(res, 415, { error: "POST /chap takes application/json" });
        let envelope;
        try { envelope = JSON.parse(await readBody(req)); } catch { return reply(res, 400, { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }); }
        return reply(res, 200, coord.dispatch(envelope));
      }
      if (url.pathname === "/api/config") return reply(res, 200, publicConfig(config));
      if (url.pathname === "/api/reviews") return reply(res, 200, { reviews: openReviews(coord, config.workspace, url.searchParams.get("reviewer") || undefined) });
      if (url.pathname.startsWith("/api/tasks/")) {
        const view = taskView(coord, config.workspace, decodeURIComponent(url.pathname.slice("/api/tasks/".length)));
        return view ? reply(res, 200, view) : reply(res, 404, { error: "unknown task" });
      }
      if (url.pathname === "/api/health") {
        const ws = coord.getWorkspace(config.workspace);
        return reply(res, 200, { ok: true, workspace: config.workspace, members: ws?.members.size ?? 0, tasks: ws?.tasks.size ?? 0, audit: ws?.audit.length ?? 0 });
      }
      if (url.pathname === "/mcp" && mcp) {
        const body = req.method === "POST" ? JSON.parse(await readBody(req)) : undefined;
        return await mcp(req, res, body);
      }
      reply(res, 404, { error: "not found" });
    } catch (e) {
      if (!res.headersSent) reply(res, 500, { error: e instanceof Error ? e.message : String(e) });
    }
  });
  return server;
}

export async function start(config) {
  const coord = await makeCoordinator(config);
  const server = await makeServer(config, coord);
  await new Promise((resolve) => server.listen(config.port, config.host, resolve));
  const base = `http://${config.host}:${config.port}`;
  console.log(`CHAP ${config.workspace} on ${base}`);
  console.log(`  desk     ${base}/`);
  console.log(`  calls    POST ${base}/chap`);
  if (config.mcp) console.log(`  mcp      ${base}/mcp`);
  console.log(`  profiles ${config.profiles.join(", ")}`);
  console.log(`  store    ${config.store}${config.chain ? ", chain on" : ""}${config.require_signatures ? ", signatures required" : ""}${config.oidc ? `, oidc ${config.oidc.issuer}` : ""}`);
  return { coord, server, base };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  start(await loadConfig()).catch((e) => { console.error(e); process.exit(1); });
}
