// The process that owns the store.
//
// It runs the CHAP coordinator on SQLite and serves five things:
//
//   GET  /              the review desk, from this package's desk/
//   POST /chap          CHAP calls as JSON-RPC, for any agent or client
//   GET  /api/...       what the desk needs that the protocol does not carry
//   GET  /analytics/    the pages chap-analytics wrote, when analytics/ exists
//   POST /mcp           an MCP server over streamable HTTP, when enabled
//
// Everything comes from a project's chap.config.json, with a few
// environment overrides noted in loadConfig. The project is the directory
// that holds the file: its analytics/ is served, and the desk names it.
//
// The server answers under its own host names only (421 otherwise), refuses
// a browser request from another origin (403), takes JSON only on POST /chap
// (415) and reads no body over the envelope limit, max_envelope_bytes in
// chap.config.json (1 MiB when unset), with a little room (413). Every
// answer carries the headers in SECURITY_HEADERS: the desk runs only its own
// scripts and styles, and no page can frame it. The analytics pages are
// written by another program, so they run in a sandbox: their scripts get
// an origin of their own, and cannot read the desk's storage or call the
// server as the desk.

import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { readFile, readdir, mkdir, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Coordinator, MemoryStore } from "@brightbeamai/chap-coordinator";
import { AGENT_MCP_TOOLS, chapMcpHttpHandler } from "@brightbeamai/chap-coordinator-mcp";

/** The desk's files, served by every project from this package. */
export const DESK_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "desk");

/** This package's version, which /mcp reports as its server's. */
const VERSION = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;

/**
 * The project's configuration: the file `path` names, by default the one
 * CHAP_CONFIG names or else chap.config.json in the working directory, with
 * the environment applied: PORT, CHAP_HOST and
 * CHAP_DB_PATH replace the port, host and store; CHAP_REQUIRE_SIGNATURES,
 * CHAP_CHAIN and CHAP_MCP (1 or true) replace the flags; CHAP_ALLOWED_HOSTS
 * adds host names, comma separated; OIDC_ISSUER, OIDC_JWKS_URL and
 * OIDC_AUDIENCE set the token verifier. `dir` is the directory that holds
 * the file: the project. A relative store path, in the file or in
 * CHAP_DB_PATH, is taken from there, and a leading ~ is the home directory,
 * so a server started from anywhere opens the project's store.
 *
 * require_signatures is true (every call after the join is signed), false,
 * or "humans" (a person's calls are signed and an agent member's need not
 * be), and CHAP_REQUIRE_SIGNATURES takes the same values. It comes out as
 * `require_signatures`, whether signatures are checked at all, and
 * `signature_policy`, "all", "humans" or null.
 */
export async function loadConfig(path = process.env.CHAP_CONFIG ?? "chap.config.json") {
  const file = resolve(path);
  const config = JSON.parse(await readFile(file, "utf8"));
  config.dir = dirname(file);
  config.port = Number(process.env.PORT ?? config.port ?? 8787);
  config.host = process.env.CHAP_HOST ?? config.host ?? "127.0.0.1";
  config.store = storePath(process.env.CHAP_DB_PATH ?? config.store ?? "./data/chap.db", config.dir);
  // A profile in the list is enforced. security-signed/1.0 requires
  // signatures, and the coordinator turns the chain on for a workspace that
  // advertises audit-scitt/1.0 whatever the flag says, so each flag reports
  // the profile as well as its own setting.
  const asked = signatureSetting(process.env.CHAP_REQUIRE_SIGNATURES || config.require_signatures);
  config.signature_policy = asked ?? (hasProfile(config.profiles, "security-signed") ? "all" : null);
  config.require_signatures = config.signature_policy !== null;
  config.chain = envFlag("CHAP_CHAIN", !!config.chain) || hasProfile(config.profiles, "audit-scitt");
  config.mcp = envFlag("CHAP_MCP", config.mcp ?? false);
  config.oidc = oidcFromEnv(config.oidc);
  config.allowed_hosts = [...(config.allowed_hosts ?? []), ...envList("CHAP_ALLOWED_HOSTS")];
  return config;
}

/** A store path as the server opens it: ":memory:" as it is, ~ as the home directory, a relative path from `dir`. */
export function storePath(store, dir) {
  if (store === ":memory:") return store;
  const home = /^~(?=$|[\\/])/.test(store) ? homedir() + store.slice(1) : store;
  return resolve(dir, home);
}

/** The signature policy require_signatures asks for: "all", "humans", or null for none. */
function signatureSetting(value) {
  if (value === undefined || value === null || value === false || value === "") return null;
  if (value === true) return "all";
  const v = String(value).toLowerCase();
  if (v === "humans") return "humans";
  if (v === "1" || v === "true") return "all";
  if (v === "0" || v === "false") return null;
  throw new Error(`require_signatures in chap.config.json, or CHAP_REQUIRE_SIGNATURES, is ${JSON.stringify(value)}; it is true, false or "humans"`);
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

/**
 * Build the coordinator from a config. Exported so tests can run in-process.
 * `enrol` lists members to join with their keys at start (see bootstrap);
 * every other entry of `extra` is a coordinator option.
 */
export async function makeCoordinator(config, { enrol = [], log = console.log, ...extra } = {}) {
  reconcileSignedProfile(config);
  reconcileOidcProfile(config, !!(config.oidc || extra.verifyOidcToken));
  const options = {
    store: await openStore(config.store),
    defaultProfiles: config.profiles,
    enableChain: !!config.chain,
    requireSignatures: !!config.require_signatures,
    // The workspace's signature policy, which a coordinator that has one
    // applies: under "humans" an agent member's call need not be signed.
    ...(config.signature_policy ? { signaturePolicy: config.signature_policy } : {}),
    ...(config.max_envelope_bytes ? { maxEnvelopeBytes: config.max_envelope_bytes } : {}),
    ...extra,
  };
  if (config.oidc && !options.verifyOidcToken) {
    const { makeOidcVerifier } = await import("./oidc.mjs");
    options.verifyOidcToken = await makeOidcVerifier(config.oidc);
  }
  const coord = new Coordinator(options);
  await coord.start();
  await bootstrap(coord, config, { enrol, log });
  return coord;
}

/**
 * Create the workspace and join the configured participants.
 *
 * `enrol` lists members whose keys are known before anyone joins, from a
 * trust policy say: { uri, type, role, display_name, keys }. Each is joined
 * with its keys first, so whoever joins later under that URI with another
 * key holds nothing; a member on record already with none of its keys is
 * reported through `log`.
 *
 * With signatures required, nobody else is joined here: each participant
 * joins itself with its own key, since a re-join cannot add a key for
 * someone. The desk does this for a human; the agent does it for itself.
 */
export async function bootstrap(coord, config, { enrol = [], log = console.log } = {}) {
  const send = (method, params) => coord.dispatch({ jsonrpc: "2.0", id: `boot-${method}`, method, params: { workspace: config.workspace, ...params } });
  if (!coord.getWorkspace(config.workspace)) {
    const r = send("workspace.create", { profiles: config.profiles, ...(config.mode ? { mode: config.mode } : {}), ...(config.mode_ceiling ? { mode_ceiling: config.mode_ceiling } : {}) });
    if (r.error) throw new Error(`workspace.create: ${r.error.message}`);
  }
  for (const m of enrol) {
    const held = coord.getWorkspace(config.workspace).members.get(m.uri);
    if (held) {
      const xs = new Set(held.keys.filter((k) => !k.revoked_at && !k.valid_until).map((k) => k.jwk.x));
      if (!m.keys.some((k) => xs.has(k.x))) log(`${m.uri} is on record in ${config.workspace} with a key the trust policy does not pin; their decisions there will not verify against it. Find out who joined under that URI.`);
      continue;
    }
    const r = send("participant.join", { from: m.uri, type: m.type, role: m.role, ...(m.display_name ? { display_name: m.display_name } : {}), jwks: { keys: m.keys } });
    if (r.error) throw new Error(`participant.join ${m.uri}: ${r.error.message}`);
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
  const log = decisionLog(ws);
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
      submission: log.submissions.get(task.id) ?? null,
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
  const submissions = new Map();
  for (const entry of ws.audit) {
    const call = entry.envelope;
    if (!call || typeof call.method !== "string") continue;
    const p = call.params ?? {};
    if (typeof p.task_id !== "string") continue;
    if (call.method === "task.complete" || call.method === "review.request") {
      // The latest submission opens the current review round. A reviewer's
      // decision names it, so an approval cannot be counted in another round.
      submissions.set(p.task_id, { seq: entry.seq, arrived: entry.arrived, envelope: call });
      continue;
    }
    if (!call.method.startsWith("decide.")) continue;
    const row = {
      seq: entry.seq, ts: entry.arrived, reviewer: p.from, kind: call.method.slice("decide.".length),
      comment: p.comment ?? null, rationale: p.rationale ?? null, tags: Array.isArray(p.tags) ? p.tags : [],
      request_revision: p.request_revision === true, round: p.round ?? null,
      comments: Array.isArray(p.comments) ? p.comments : [],
    };
    if (!byTask.has(p.task_id)) byTask.set(p.task_id, []);
    byTask.get(p.task_id).push(row);
  }
  byTask.submissions = submissions;
  return byTask;
}

/**
 * An artefact without its bulk: a code change without the whole-file
 * contents beside its patch, a branch without each commit's patch. The
 * lists the desk polls carry this; one task's view carries everything.
 */
function slim(artefact) {
  if (!artefact || typeof artefact !== "object" || Array.isArray(artefact)) return artefact;
  const lean = (files) => (Array.isArray(files) ? files.map((f) => (f && typeof f === "object" ? Object.fromEntries(Object.entries(f).filter(([k]) => k !== "before" && k !== "after")) : f)) : files);
  const out = { ...artefact };
  if (Array.isArray(out.files)) out.files = lean(out.files);
  if (Array.isArray(out.commits)) out.commits = out.commits.map((c) => (c && typeof c === "object" ? { ...Object.fromEntries(Object.entries(c).filter(([k]) => k !== "patch")), files: lean(c.files) } : c));
  if (out.since && typeof out.since === "object") out.since = { head: out.since.head };
  delete out.context;
  return out;
}

/**
 * A task as the read API shows it. `artefact` is what is or was under
 * review, whatever was decided. An override's rationale lives on the
 * override artefact, and is put on the decision here as `rationale`.
 * `decision_log` is every decision on the task, across review rounds.
 * With `brief`, the artefact is slimmed and the submission is named by its
 * place on the chain only; the desk reads the full view of the task it shows.
 */
function viewOf(ws, task, log = null, { brief = false } = {}) {
  let review = task.review ?? null;
  if (review) {
    review = { ...review, decisions: review.decisions.map((d) => {
      if (!d.override_artefact_id) return d;
      const override = ws.overrides.get(d.override_artefact_id);
      return { ...d, rationale: override?.rationale ?? null, comment: d.comment ?? override?.rationale ?? undefined };
    }) };
  }
  const submission = log?.submissions?.get(task.id) ?? null;
  const artefact = task.pending_artefact ?? task.output ?? null;
  return {
    task_id: task.id, kind: task.kind, state: task.state, assignee: task.assignee, mode: task.mode,
    created_at: task.created_at, updated_at: task.updated_at, input: task.input,
    output: brief ? slim(task.output ?? null) : (task.output ?? null), artefact: brief ? slim(artefact) : artefact,
    review, decision_log: log ? (log.get(task.id) ?? []) : undefined,
    submission: brief && submission ? { seq: submission.seq, arrived: submission.arrived } : submission, history: task.history,
    ...(brief ? { brief: true } : {}),
  };
}

/**
 * The workspace's tasks, newest first, narrowed by kind and by state when
 * given (several states comma separated). `limit` caps the list; the
 * default is every task.
 */
export function listTasks(coord, workspace, { kind, state, limit, brief = false } = {}) {
  const ws = coord.getWorkspace(workspace);
  if (!ws) return [];
  const states = state ? new Set(String(state).split(",").map((x) => x.trim()).filter(Boolean)) : null;
  const log = decisionLog(ws);
  const out = [];
  for (const task of ws.tasks.values()) {
    if (kind && task.kind !== kind) continue;
    if (states && !states.has(task.state)) continue;
    out.push(viewOf(ws, task, log, { brief }));
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
  // Every member's type and public keys, so a checker can tell a person
  // from an agent by the workspace's own record.
  const members = {};
  for (const [uri, m] of ws.members) members[uri] = { type: m.type, role: m.role, keys: (m.keys ?? []).filter((k) => !k.revoked_at).map((k) => k.jwk) };
  return { workspace, task: viewOf(ws, task, decisionLog(ws)), submissions, decisions, keys, members, chain_head: ws.chain_head ?? null, chain_enabled: !!ws.chain_enabled };
}

export function publicConfig(config) {
  return {
    workspace: config.workspace,
    profiles: config.profiles,
    humans: (config.humans ?? []).map(({ uri, display_name, email, role }) => ({ uri, display_name, ...(email ? { email } : {}), role })),
    ...(config.review_at ? { review_at: config.review_at } : {}),
    max_envelope_bytes: config.max_envelope_bytes ?? 1_048_576,
    agent: config.agent ?? null,
    require_signatures: !!config.require_signatures,
    signature_policy: config.signature_policy ?? null,
    chain_enabled: !!config.chain,
    persistent: config.store !== ":memory:",
    // Where this project lives, so a prompt the desk writes can name its commands.
    gate_dir: config.dir,
    oidc: config.oidc ? { issuer: config.oidc.issuer } : null,
    mcp: !!config.mcp,
  };
}

// -- HTTP ---------------------------------------------------------------------

/**
 * The headers on every answer. The policy lets the desk load its own
 * scripts, styles and images and call its own server, and nothing else: no
 * inline script or style, no plugin, no form posted anywhere, no frame
 * around it.
 */
export const SECURITY_HEADERS = {
  "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
  "x-frame-options": "DENY",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
};

/** The policy for the analytics pages: their scripts run, in an origin of their own. */
export const ANALYTICS_POLICY = "sandbox allow-scripts; frame-ancestors 'none'";

function reply(res, status, body, type = "application/json", policy = SECURITY_HEADERS["content-security-policy"]) {
  res.writeHead(status, { ...SECURITY_HEADERS, "content-security-policy": policy, "content-type": type });
  res.end(type === "application/json" && !Buffer.isBuffer(body) ? JSON.stringify(body) : body);
}

/** Largest request body read: a little above the coordinator's envelope limit. */
const bodyLimit = (config) => (config.max_envelope_bytes ?? 1_048_576) + 64 * 1024;

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

async function readBody(req, limit) {
  const chunks = [];
  let size = 0;
  for await (const c of req) {
    size += c.length;
    if (size > limit) throw new BodyTooLarge(`the request body is over ${limit} bytes`);
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

// What the agent's MCP connection offers: AGENT_MCP_TOOLS, the tools the
// agent's own work needs, from @brightbeamai/chap-coordinator-mcp.
// `participant` makes every call as the agent, so the connection cannot act
// in another member's name.
export { AGENT_MCP_TOOLS };

/** The paths under /api/ the desk reads. */
const API_PATHS = new Set(["/api/config", "/api/reviews", "/api/tasks", "/api/health"]);

/** A redirect, with the headers every answer carries. */
function redirect(res, location) {
  res.writeHead(308, { ...SECURITY_HEADERS, location });
  res.end();
}

/**
 * The request handler for one project: the desk, POST /chap, the read API,
 * the analytics pages and /mcp, behind the host, origin, type and size
 * checks. `next`, when given, is called for a path the handler does not
 * serve, as Express passes it, and the checks apply to the handler's own
 * paths only; without it such a path is answered 404. `basePath` serves
 * everything under a prefix, "/review" say, for a server whose own routes
 * hold the rest; the desk's paths are relative, so it works under any.
 */
export async function makeHandler(config, coord, { basePath = "" } = {}) {
  const prefix = basePath.replace(/\/+$/, "");
  if (prefix && !/^\/[^?#]*$/.test(prefix)) throw new Error(`basePath is a path that starts with /: ${basePath}`);
  // When a desk last asked for the health of the gate: a command that opens
  // a review leaves an open desk to show it, and opens one otherwise.
  let deskSeenAt = 0;
  const desk = await readDirectory(DESK_DIR);
  if (!desk.has("index.html")) throw new Error(`The desk is missing: ${join(DESK_DIR, "index.html")}`);
  // The pages chap-analytics writes are read on each request, so a report
  // regenerated while the server runs is served as it is now.
  const analyticsDir = join(config.dir, "analytics");
  let mcp = null;
  if (config.mcp && !config.agent?.uri) {
    throw new Error("chap.config.json turns on mcp, and /mcp acts as the agent: set agent.uri");
  }
  if (config.mcp) {
    // Stateless: the MCP client keeps no session with this process. Both
    // protocol eras are answered, the handshake one and 2026-07-28.
    mcp = chapMcpHttpHandler(coord, { name: config.workspace, version: VERSION,
      participant: config.agent.uri, toolFilter: (name) => AGENT_MCP_TOOLS.has(name), maxBodyBytes: bodyLimit(config) });
  }

  /** Whether a path, below any prefix, is one of the handler's own. */
  const ours = (path) => path === "/" || path === "/desk" || path === "/index.html" || desk.has(path.slice(1))
    || path.startsWith("/analytics/") || path === "/chap" || API_PATHS.has(path) || path.startsWith("/api/tasks/") || (path === "/mcp" && !!mcp);

  return async (req, res, next) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    let path = url.pathname;
    if (prefix) {
      if (path === prefix) return redirect(res, `${prefix}/${url.search}`);
      if (!path.startsWith(`${prefix}/`)) return next ? next() : reply(res, 404, { error: "not found" });
      path = path.slice(prefix.length);
    } else if (path === "/" && req.baseUrl && !String(req.originalUrl ?? "").split("?")[0].endsWith("/")) {
      // Express mounted the handler at req.baseUrl and took it off the
      // path; the desk's paths are relative, so its page is at the mount
      // point with a slash.
      return redirect(res, `${req.baseUrl}/${url.search}`);
    }
    if (next && !ours(path)) return next();
    try {
      if (foreignHost(req, config)) return reply(res, 421, { error: "unknown host; set allowed_hosts in chap.config.json, or CHAP_ALLOWED_HOSTS, to serve under another name" });
      if (foreignOrigin(req, config)) return reply(res, 403, { error: "cross-origin requests are refused" });
      if (path === "/" || path === "/desk" || path === "/index.html") {
        const page = desk.get("index.html");
        return reply(res, 200, page.body, page.type);
      }
      if (req.method === "GET" && desk.has(path.slice(1))) {
        const file = desk.get(path.slice(1));
        return reply(res, 200, file.body, file.type);
      }
      if (req.method === "GET" && path.startsWith("/analytics/")) {
        let name;
        try { name = decodeURIComponent(path.slice("/analytics/".length)) || "index.html"; } catch { return reply(res, 400, { error: "a malformed escape in the path" }); }
        if (name.includes("\0")) return reply(res, 404, { error: "not found" });
        const type = TYPES[extname(name)];
        if (!type || name.includes("/") || name.includes("\\") || name.startsWith(".")) return reply(res, 404, { error: "not found" });
        try {
          return reply(res, 200, await readFile(join(analyticsDir, name)), type, ANALYTICS_POLICY);
        } catch {
          return reply(res, 404, { error: "no analytics page by that name; run the analytics script to write them" });
        }
      }
      if (path === "/chap" && req.method === "POST") {
        if (!(req.headers["content-type"] ?? "").toLowerCase().startsWith("application/json")) return reply(res, 415, { error: "POST /chap takes application/json" });
        let envelope;
        try { envelope = JSON.parse(await readBody(req, bodyLimit(config))); } catch (e) {
          if (e instanceof BodyTooLarge) throw e;
          return reply(res, 400, { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
        }
        return reply(res, 200, coord.dispatch(envelope));
      }
      if (path === "/api/config") return reply(res, 200, publicConfig(config));
      if (path === "/api/reviews") return reply(res, 200, { reviews: openReviews(coord, config.workspace, url.searchParams.get("reviewer") || undefined) });
      if (path === "/api/tasks") {
        const rawLimit = url.searchParams.get("limit");
        if (rawLimit !== null && !/^\d+$/.test(rawLimit)) return reply(res, 400, { error: "limit is a whole number" });
        const limit = Number(rawLimit ?? 0) || undefined;
        const brief = url.searchParams.get("brief") === "1";
        return reply(res, 200, { tasks: listTasks(coord, config.workspace, { kind: url.searchParams.get("kind") || undefined, state: url.searchParams.get("state") || undefined, limit, brief }) });
      }
      if (path.startsWith("/api/tasks/")) {
        let parts;
        try { parts = path.slice("/api/tasks/".length).split("/").map(decodeURIComponent); } catch { return reply(res, 400, { error: "a malformed escape in the path" }); }
        const [id, ...rest] = parts;
        if (rest.length === 1 && rest[0] === "evidence") {
          const evidence = taskEvidence(coord, config.workspace, id);
          return evidence ? reply(res, 200, evidence) : reply(res, 404, { error: "unknown task" });
        }
        if (rest.length) return reply(res, 404, { error: "not found" });
        const view = taskView(coord, config.workspace, id);
        return view ? reply(res, 200, view) : reply(res, 404, { error: "unknown task" });
      }
      if (path === "/api/health") {
        const ws = coord.getWorkspace(config.workspace);
        if (url.searchParams.get("desk") === "1") deskSeenAt = Date.now();
        return reply(res, 200, { ok: true, workspace: config.workspace, members: ws?.members.size ?? 0, tasks: ws?.tasks.size ?? 0, audit: ws?.audit.length ?? 0, desk_seen_ms: deskSeenAt ? Date.now() - deskSeenAt : null });
      }
      if (path === "/mcp" && mcp) {
        for (const [name, value] of Object.entries(SECURITY_HEADERS)) res.setHeader(name, value);
        let body;
        if (req.method === "POST") {
          try { body = JSON.parse(await readBody(req, bodyLimit(config))); } catch (e) {
            if (e instanceof BodyTooLarge) throw e;
            return reply(res, 400, { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
          }
        }
        return await mcp(req, res, body);
      }
      if (next) return next();
      reply(res, 404, { error: "not found" });
    } catch (e) {
      if (res.headersSent) return;
      if (e instanceof BodyTooLarge) return reply(res, 413, { error: e.message });
      reply(res, 500, { error: e instanceof Error ? e.message : String(e) });
    }
  };
}

/** A Node HTTP server for one project's handler. */
export async function makeServer(config, coord) {
  return createServer(await makeHandler(config, coord));
}

/** The config, the coordinator and the handler, from what createChapServer and chapHandler take. */
async function prepare({ config, configPath, coordinator, basePath, enrol = [], log = console.log } = {}) {
  config ??= await loadConfig(configPath);
  const coord = coordinator ?? await makeCoordinator(config, { enrol, log });
  return { config, coord, handler: await makeHandler(config, coord, { basePath }) };
}

/**
 * The project's server, ready to listen: the coordinator on the configured
 * store with the workspace created, its handler, and a Node HTTP server.
 * `config` is what loadConfig returns; `configPath` reads one instead;
 * `coordinator` serves one the caller made; `basePath` serves everything
 * under a prefix; `enrol` joins members with known keys first (bootstrap).
 * `listen()` takes the configured port and host and prints where it
 * answers; `close()` stops it.
 */
export async function createChapServer(options = {}) {
  const { config, coord, handler } = await prepare(options);
  const server = createServer(handler);
  return {
    config, coord, handler, server,
    async listen({ port = config.port, host = config.host, log = console.log } = {}) {
      await new Promise((resolve) => server.listen(port, host, resolve));
      const base = `http://${host}:${server.address().port}`;
      log(`CHAP ${config.workspace} on ${base}`);
      log(`  desk     ${base}/`);
      log(`  calls    POST ${base}/chap`);
      if (config.mcp) log(`  mcp      ${base}/mcp`);
      log(`  profiles ${config.profiles.join(", ")}`);
      log(`  store    ${config.store}${config.chain ? ", chain on" : ""}${config.require_signatures ? ", signatures required" : ""}${config.oidc ? `, oidc ${config.oidc.issuer}` : ""}`);
      return { base };
    },
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

/**
 * A handler to mount in a server of the application's own: Node's http,
 * Express, or Fastify through its raw request and response. It takes what
 * createChapServer takes and builds the coordinator the same way;
 * createChapServer(...).handler is the same handler with the coordinator
 * beside it.
 */
export async function chapHandler(options = {}) {
  return (await prepare(options)).handler;
}

/**
 * Serve a project: its configuration from `configPath`, or from CHAP_CONFIG,
 * or from chap.config.json in `dir`. Returns { coord, server, base }.
 */
export async function serve({ dir = process.cwd(), configPath } = {}) {
  return start(await loadConfig(configPath ?? process.env.CHAP_CONFIG ?? join(dir, "chap.config.json")));
}

/** Start the server for a config: the coordinator, the handler, and the port. Returns { coord, server, base }. */
export async function start(config) {
  const chap = await createChapServer({ config });
  const { base } = await chap.listen();
  return { coord: chap.coord, server: chap.server, base };
}
