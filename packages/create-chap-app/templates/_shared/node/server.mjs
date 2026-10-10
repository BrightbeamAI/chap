// The process that owns the store.
//
// It runs the CHAP coordinator on SQLite and serves five things:
//
//   GET  /              the review desk, with the files beside it in desk/
//   POST /chap          CHAP calls as JSON-RPC, for any agent or client
//   GET  /api/...       what the desk needs that the protocol does not carry
//   GET  /analytics/    the pages chap-analytics wrote, when analytics/ exists
//   POST /mcp           an MCP server over streamable HTTP, when enabled
//
// Everything comes from chap.config.json next to this file, with a few
// environment overrides noted in loadConfig. Run it with `node server.mjs`.
//
// The server answers under its own host names only (421 otherwise), refuses
// a browser request from another origin (403), takes JSON only on POST /chap
// (415) and reads no body over MAX_BODY_BYTES (413).

import { createServer } from "node:http";
import { readFile, readdir, mkdir, stat } from "node:fs/promises";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Coordinator, MemoryStore } from "@brightbeamai/chap-coordinator";

const here = dirname(fileURLToPath(import.meta.url));

/**
 * chap.config.json with the environment applied: PORT, CHAP_HOST and
 * CHAP_DB_PATH replace the port, host and store; CHAP_REQUIRE_SIGNATURES,
 * CHAP_CHAIN and CHAP_MCP (1 or true) replace the flags; CHAP_ALLOWED_HOSTS
 * adds host names, comma separated; OIDC_ISSUER, OIDC_JWKS_URL and
 * OIDC_AUDIENCE set the token verifier.
 */
export async function loadConfig(path = join(here, "chap.config.json")) {
  const config = JSON.parse(await readFile(path, "utf8"));
  config.port = Number(process.env.PORT ?? config.port ?? 8787);
  config.host = process.env.CHAP_HOST ?? config.host ?? "127.0.0.1";
  config.store = process.env.CHAP_DB_PATH ?? config.store ?? "./data/chap.db";
  // A profile in the list is enforced. security-signed/1.0 requires
  // signatures, and the coordinator turns the chain on for a workspace that
  // advertises audit-scitt/1.0 whatever the flag says, so each flag reports
  // the profile as well as its own setting.
  config.require_signatures = envFlag("CHAP_REQUIRE_SIGNATURES", !!config.require_signatures) || hasProfile(config.profiles, "security-signed");
  config.chain = envFlag("CHAP_CHAIN", !!config.chain) || hasProfile(config.profiles, "audit-scitt");
  config.mcp = envFlag("CHAP_MCP", config.mcp ?? false);
  config.oidc = oidcFromEnv(config.oidc);
  config.allowed_hosts = [...(config.allowed_hosts ?? []), ...envList("CHAP_ALLOWED_HOSTS")];
  return config;
}

/** Whether a profile list names `name` at any version. */
export function hasProfile(profiles, name) {
  return (profiles ?? []).some((p) => p === name || p.startsWith(name + "/"));
}

function envFlag(name, fallback) {
  const v = process.env[name];
  if (v === undefined || v === "") return fallback;
  return v === "1" || v.toLowerCase() === "true";
}

/** A comma-separated environment variable as a list, empty when unset. */
function envList(name) {
  return (process.env[name] ?? "").split(",").map((s) => s.trim()).filter(Boolean);
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

/**
 * security-signed/1.0 is enforced by requireSignatures, and the coordinator
 * adds the profile where signatures are required (SPECIFICATION 15.1, item
 * 3). loadConfig already requires signatures where the profile is listed;
 * this adds the profile where the flag alone asked for them, so the console,
 * /api/config and the descriptor say the same thing.
 */
export function reconcileSignedProfile(config) {
  if (config.require_signatures && !hasProfile(config.profiles, "security-signed")) {
    config.profiles = [...config.profiles, "security-signed/1.0"];
    console.log("security-signed/1.0 added to the profiles: signatures are required.");
  }
}

/** Build the coordinator from a config. Exported so tests can run in-process. */
export async function makeCoordinator(config, extra = {}) {
  reconcileSignedProfile(config);
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

/** One task, for an agent waiting on a decision and for the desk's lists. */
export function taskView(coord, workspace, taskId) {
  const ws = coord.getWorkspace(workspace);
  const task = ws?.tasks.get(taskId);
  if (!task) return null;
  return viewOf(ws, task, decisionLog(ws));
}

/**
 * Every accepted decide.* call on the chain, by task, in order. A review
 * opened again with review.request starts with no decisions, so the task's
 * own review holds the current round only; this holds every round, with
 * the note, the rationale, the tags and whether a revision was asked for.
 * One scan of the log per request.
 */
function decisionLog(ws) {
  const byTask = new Map();
  for (const entry of ws.audit) {
    const call = entry.envelope;
    if (!call || typeof call.method !== "string" || !call.method.startsWith("decide.")) continue;
    const p = call.params ?? {};
    if (typeof p.task_id !== "string") continue;
    const row = {
      seq: entry.seq, ts: entry.arrived, reviewer: p.from, kind: call.method.slice("decide.".length),
      comment: p.comment ?? null, rationale: p.rationale ?? null, tags: Array.isArray(p.tags) ? p.tags : [],
      request_revision: p.request_revision === true,
    };
    if (!byTask.has(p.task_id)) byTask.set(p.task_id, []);
    byTask.get(p.task_id).push(row);
  }
  return byTask;
}

/**
 * A task as the read API shows it. `artefact` is what is or was under
 * review, whatever was decided. An override's rationale lives on the
 * override artefact, and is put on the decision here as `rationale`.
 * `decision_log` is every decision on the task, across review rounds.
 */
function viewOf(ws, task, log = null) {
  let review = task.review ?? null;
  if (review) {
    review = { ...review, decisions: review.decisions.map((d) => {
      if (!d.override_artefact_id) return d;
      const override = ws.overrides.get(d.override_artefact_id);
      return { ...d, rationale: override?.rationale ?? null, comment: d.comment ?? override?.rationale ?? undefined };
    }) };
  }
  return {
    task_id: task.id, kind: task.kind, state: task.state, assignee: task.assignee, mode: task.mode,
    created_at: task.created_at, updated_at: task.updated_at, input: task.input,
    output: task.output ?? null, artefact: task.pending_artefact ?? task.output ?? null,
    review, decision_log: log ? (log.get(task.id) ?? []) : undefined, history: task.history,
  };
}

/**
 * The workspace's tasks, newest first, narrowed by kind and by state when
 * given (several states comma separated). `limit` caps the list; the
 * default is every task.
 */
export function listTasks(coord, workspace, { kind, state, limit } = {}) {
  const ws = coord.getWorkspace(workspace);
  if (!ws) return [];
  const states = state ? new Set(String(state).split(",").map((x) => x.trim()).filter(Boolean)) : null;
  const log = decisionLog(ws);
  const out = [];
  for (const task of ws.tasks.values()) {
    if (kind && task.kind !== kind) continue;
    if (states && !states.has(task.state)) continue;
    out.push(viewOf(ws, task, log));
  }
  out.sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  return limit ? out.slice(0, limit) : out;
}

/**
 * The evidence behind a task's decisions: the task, the accepted
 * task.complete and review.request entries from the chain for it (each
 * carries the artefact as submitted, and each opens a review round), the
 * accepted decide.* entries (signed envelopes under security-signed/1.0),
 * the keys on record for the reviewers who decided and for the assignee,
 * and the chain head. A committer writes this beside what it commits, and
 * a verifier reads it.
 */
export function taskEvidence(coord, workspace, taskId) {
  const ws = coord.getWorkspace(workspace);
  const task = ws?.tasks.get(taskId);
  if (!task) return null;
  const decisions = [];
  const submissions = [];
  for (const entry of ws.audit) {
    const call = entry.envelope;
    if (!call || typeof call.method !== "string" || call.params?.task_id !== taskId) continue;
    const row = { seq: entry.seq, arrived: entry.arrived, prev_hash: entry.prev_hash, envelope: call };
    if (call.method.startsWith("decide.")) decisions.push(row);
    else if (call.method === "task.complete" || call.method === "review.request") submissions.push(row);
  }
  const keys = {};
  for (const uri of [...decisions.map((d) => d.envelope.params?.from), task.assignee]) {
    const member = typeof uri === "string" ? ws.members.get(uri) : undefined;
    if (member && !(uri in keys)) keys[uri] = (member.keys ?? []).map((k) => k.jwk);
  }
  return { workspace, task: viewOf(ws, task, decisionLog(ws)), submissions, decisions, keys, chain_head: ws.chain_head ?? null, chain_enabled: !!ws.chain_enabled };
}

export function publicConfig(config) {
  return {
    workspace: config.workspace,
    profiles: config.profiles,
    humans: (config.humans ?? []).map(({ uri, display_name, role }) => ({ uri, display_name, role })),
    agent: config.agent ?? null,
    require_signatures: !!config.require_signatures,
    chain_enabled: !!config.chain,
    persistent: config.store !== ":memory:",
    oidc: config.oidc ? { issuer: config.oidc.issuer } : null,
    mcp: !!config.mcp,
  };
}

// -- HTTP ---------------------------------------------------------------------

function reply(res, status, body, type = "application/json") {
  res.writeHead(status, { "content-type": type });
  res.end(type === "application/json" && !Buffer.isBuffer(body) ? JSON.stringify(body) : body);
}

/** Largest request body read, a little above the coordinator's envelope limit. */
const MAX_BODY_BYTES = 1_100_000;

/**
 * The host names this server answers to: its loopback names on its own
 * port, the configured host, and `allowed_hosts` from chap.config.json or
 * CHAP_ALLOWED_HOSTS, for a name a proxy or a Compose service reaches it by.
 */
function allowedHosts(req, config) {
  const port = req.socket.localPort;
  const allowed = new Set([`127.0.0.1:${port}`, `localhost:${port}`, `[::1]:${port}`, `${config.host}:${port}`.toLowerCase()]);
  for (const h of config.allowed_hosts ?? []) allowed.add(h.toLowerCase());
  return allowed;
}

/**
 * A request whose Host header is not one of this server's own names. A page
 * that resolves its own name to this address (DNS rebinding) arrives with
 * that name as the Host, and is refused before any route.
 */
function foreignHost(req, config) {
  return !allowedHosts(req, config).has((req.headers.host ?? "").toLowerCase());
}

/**
 * A browser request from another origin. The desk is served from this
 * process, so its requests carry one of this server's names as the origin's
 * host, under http or under the https a proxy in front terminates. A page
 * on another origin gets no cross-origin headers and its calls are refused,
 * so an open tab elsewhere cannot decide as the reviewer. A non-browser
 * client sends no Origin header.
 */
function foreignOrigin(req, config) {
  const origin = req.headers.origin;
  if (!origin) return false;
  let host;
  try { host = new URL(origin).host.toLowerCase(); } catch { return true; }
  const allowed = allowedHosts(req, config);
  allowed.add((req.headers.host ?? "").toLowerCase());
  return !allowed.has(host);
}

class BodyTooLarge extends Error {}

async function readBody(req) {
  const chunks = [];
  let size = 0;
  for await (const c of req) {
    size += c.length;
    if (size > MAX_BODY_BYTES) throw new BodyTooLarge(`the request body is over ${MAX_BODY_BYTES} bytes`);
    chunks.push(c);
  }
  return Buffer.concat(chunks).toString("utf8");
}

const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png",
  ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8", ".jsonl": "application/x-ndjson",
};

/** The files of a directory, read into memory once, keyed by name. Subdirectories are left out. */
async function readDirectory(dir) {
  const files = new Map();
  let names = [];
  try { names = await readdir(dir); } catch { return files; }
  for (const name of names) {
    const type = TYPES[extname(name)];
    if (!type || name.startsWith(".")) continue;
    const path = join(dir, name);
    if (!(await stat(path)).isFile()) continue;
    files.set(name, { body: await readFile(path), type });
  }
  return files;
}

export async function makeServer(config, coord) {
  const desk = await readDirectory(join(here, "desk"));
  if (!desk.has("index.html")) throw new Error(`The desk is missing: ${join(here, "desk", "index.html")}`);
  // The pages chap-analytics writes are read on each request, so a report
  // regenerated while the server runs is served as it is now.
  const analyticsDir = join(here, "analytics");
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
      if (foreignHost(req, config)) return reply(res, 421, { error: "unknown host; set allowed_hosts in chap.config.json, or CHAP_ALLOWED_HOSTS, to serve under another name" });
      if (foreignOrigin(req, config)) return reply(res, 403, { error: "cross-origin requests are refused" });
      if (url.pathname === "/" || url.pathname === "/desk" || url.pathname === "/index.html") {
        const page = desk.get("index.html");
        return reply(res, 200, page.body, page.type);
      }
      if (req.method === "GET" && desk.has(url.pathname.slice(1))) {
        const file = desk.get(url.pathname.slice(1));
        return reply(res, 200, file.body, file.type);
      }
      if (req.method === "GET" && url.pathname.startsWith("/analytics/")) {
        const name = decodeURIComponent(url.pathname.slice("/analytics/".length)) || "index.html";
        const type = TYPES[extname(name)];
        if (!type || name.includes("/") || name.includes("\\") || name.startsWith(".")) return reply(res, 404, { error: "not found" });
        try {
          return reply(res, 200, await readFile(join(analyticsDir, name)), type);
        } catch {
          return reply(res, 404, { error: "no analytics page by that name; run the analytics script to write them" });
        }
      }
      if (url.pathname === "/chap" && req.method === "POST") {
        if (!(req.headers["content-type"] ?? "").toLowerCase().startsWith("application/json")) return reply(res, 415, { error: "POST /chap takes application/json" });
        let envelope;
        try { envelope = JSON.parse(await readBody(req)); } catch (e) {
          if (e instanceof BodyTooLarge) throw e;
          return reply(res, 400, { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
        }
        return reply(res, 200, coord.dispatch(envelope));
      }
      if (url.pathname === "/api/config") return reply(res, 200, publicConfig(config));
      if (url.pathname === "/api/reviews") return reply(res, 200, { reviews: openReviews(coord, config.workspace, url.searchParams.get("reviewer") || undefined) });
      if (url.pathname === "/api/tasks") {
        const limit = Number(url.searchParams.get("limit") ?? 0) || undefined;
        return reply(res, 200, { tasks: listTasks(coord, config.workspace, { kind: url.searchParams.get("kind") || undefined, state: url.searchParams.get("state") || undefined, limit }) });
      }
      if (url.pathname.startsWith("/api/tasks/")) {
        const rest = url.pathname.slice("/api/tasks/".length);
        const [id, part] = rest.split("/").map(decodeURIComponent);
        if (part === "evidence") {
          const evidence = taskEvidence(coord, config.workspace, id);
          return evidence ? reply(res, 200, evidence) : reply(res, 404, { error: "unknown task" });
        }
        if (part !== undefined) return reply(res, 404, { error: "not found" });
        const view = taskView(coord, config.workspace, id);
        return view ? reply(res, 200, view) : reply(res, 404, { error: "unknown task" });
      }
      if (url.pathname === "/api/health") {
        const ws = coord.getWorkspace(config.workspace);
        return reply(res, 200, { ok: true, workspace: config.workspace, members: ws?.members.size ?? 0, tasks: ws?.tasks.size ?? 0, audit: ws?.audit.length ?? 0 });
      }
      if (url.pathname === "/mcp" && mcp) {
        let body;
        if (req.method === "POST") {
          try { body = JSON.parse(await readBody(req)); } catch (e) {
            if (e instanceof BodyTooLarge) throw e;
            return reply(res, 400, { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
          }
        }
        return await mcp(req, res, body);
      }
      reply(res, 404, { error: "not found" });
    } catch (e) {
      if (res.headersSent) return;
      if (e instanceof BodyTooLarge) return reply(res, 413, { error: e.message });
      reply(res, 500, { error: e instanceof Error ? e.message : String(e) });
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
