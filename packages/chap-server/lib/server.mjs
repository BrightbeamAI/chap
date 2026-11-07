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
import { keyPathFor, publicJwkOf } from "./keys.mjs";
import { makeNotifier, notifySettings } from "./notify.mjs";

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
 * adds host names, comma separated; OIDC_ISSUER, OIDC_CLIENT_ID and
 * OIDC_CLIENT_SECRET set the sign-in and the token verifier (oidcSettings).
 * `dir` is the directory that holds
 * the file: the project. A relative store path, in the file or in
 * CHAP_DB_PATH, is taken from there, and a leading ~ is the home directory,
 * so a server started from anywhere opens the project's store.
 *
 * require_signatures is true (every call after the join is signed), false,
 * or "humans" (a person's calls are signed and an agent member's need not
 * be), and CHAP_REQUIRE_SIGNATURES takes the same values. It comes out as
 * `require_signatures`, whether signatures are checked at all, and
 * `signature_policy`, "all", "humans" or null. desk_signing, or
 * CHAP_DESK_SIGNING, says how the desk signs: "auto", "webauthn" or
 * "browser-key", the last where none is given. notify tells people that a
 * review or a question waits (notify.mjs), with a link to the desk at
 * desk_url, or CHAP_DESK_URL, which is by default where the server listens.
 */
export async function loadConfig(path = process.env.CHAP_CONFIG ?? "chap.config.json") {
  const file = resolve(path);
  const config = JSON.parse(await readFile(file, "utf8"));
  config.dir = dirname(file);
  config.port = Number(process.env.PORT ?? config.port ?? 8792);
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
  config.desk_signing = deskSigning(process.env.CHAP_DESK_SIGNING || config.desk_signing);
  config.notify = notifySettings(config.notify);
  config.desk_url = process.env.CHAP_DESK_URL || config.desk_url || null;
  config.oidc = oidcSettings(config.oidc);
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

/** What each signature policy asks, as the console says it. */
const SIGNING = {
  all: "every call after the join is signed",
  humans: "people sign every call; an agent member's calls need no signature",
  null: "off: whoever reaches this server can decide as a person",
};

/**
 * How the desk signs where signatures are required: "auto", a passkey where
 * the browser has one that verifies its user and a key kept in the browser
 * otherwise; "webauthn", a passkey always; "browser-key", the kept key. A
 * configuration that names none has the kept key, as before 0.3.5.
 */
function deskSigning(value) {
  if (value === undefined || value === null || value === "") return "browser-key";
  if (["auto", "webauthn", "browser-key"].includes(value)) return value;
  throw new Error(`desk_signing in chap.config.json, or CHAP_DESK_SIGNING, is ${JSON.stringify(value)}; it is "auto", "webauthn" or "browser-key"`);
}

/** How the desk signs, as the console says it. */
const DESK_SIGNING = {
  auto: "the desk signs with a passkey where the browser has one, and otherwise with a key it keeps in the browser",
  webauthn: "the desk signs with a passkey",
  "browser-key": "the desk signs with a key it keeps in the browser",
};

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

/**
 * The OIDC provider the desk signs people in with, from `oidc` in
 * chap.config.json and the environment: the issuer, and the client id the
 * provider gave the desk, which every ID token must be issued to. Tokens
 * are checked against the keys the issuer's discovery document names. A
 * client secret is for a provider that gives a web redirect only to a
 * client with one; the server sends it with the code and never to the
 * browser. OIDC_AUDIENCE, the name before 0.3.5, is read as the client id.
 */
function oidcSettings(configured) {
  const issuer = process.env.OIDC_ISSUER || configured?.issuer;
  if (!issuer) return null;
  const clientId = process.env.OIDC_CLIENT_ID || configured?.client_id || process.env.OIDC_AUDIENCE || configured?.audience;
  if (!clientId) throw new Error(`The OIDC issuer ${issuer} is set and no client id is: set OIDC_CLIENT_ID, or "client_id" under "oidc" in chap.config.json, to the client id the provider gave the desk. Every ID token must be issued to it.`);
  const secret = process.env.OIDC_CLIENT_SECRET || configured?.client_secret || null;
  return { issuer, client_id: clientId, ...(secret ? { client_secret: secret } : {}) };
}

/** The provider for each oidc setting, read once: its discovery document, the token check, and the check bound to the configured people. */
const providers = new WeakMap();

/**
 * The OIDC provider a configuration names: { discovery, verify, people },
 * where people(token) gives the claims of a token for a person
 * chap.config.json names, with chap_participant_uri set, or why it is
 * refused (oidc.mjs).
 */
async function oidcProvider(config) {
  if (!providers.has(config.oidc)) {
    providers.set(config.oidc, (async () => {
      const { discover, forPeople, makeOidcVerifier } = await import("./oidc.mjs");
      const discovery = await discover(config.oidc.issuer);
      const verify = await makeOidcVerifier({ issuer: config.oidc.issuer, audience: config.oidc.client_id, discovery });
      config.oidc.discovery = discovery;
      return { discovery, verify, people: forPeople(verify.check, config.humans ?? []) };
    })());
  }
  return providers.get(config.oidc);
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
    // A token joins a person only as someone chap.config.json names.
    const { people } = await oidcProvider(config);
    options.verifyOidcToken = (token) => people(token).claims ?? null;
  }
  const coord = new Coordinator(options);
  await coord.start();
  await bootstrap(coord, config, { enrol, log });
  if (config.notify) coord.onAudit(makeNotifier(config.notify, config.workspace, { deskUrl: () => config.desk_url, log, userAgent: `chap-server/${VERSION}` }));
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
 * someone. The desk does this for a human; the agent does it for itself,
 * or, under the humans policy, /mcp does it for the agent it acts as.
 */
export async function bootstrap(coord, config, { enrol = [], log = console.log } = {}) {
  const send = (method, params) => coord.dispatch({ jsonrpc: "2.0", id: `boot-${method}`, method, params: { workspace: config.workspace, ...params } });
  if (!coord.getWorkspace(config.workspace)) {
    const r = send("workspace.create", { profiles: config.profiles, ...(config.mode ? { mode: config.mode } : {}), ...(config.mode_ceiling ? { mode_ceiling: config.mode_ceiling } : {}) });
    if (r.error) throw new Error(`workspace.create: ${r.error.message}`);
  }
  settleSignatures(coord.getWorkspace(config.workspace), config, log);
  config.enrolled = enrol.map((m) => m.uri);
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
  if (config.require_signatures) {
    // Under the humans policy the agent calls unsigned, which the
    // coordinator takes only from a member, so it joins now: /mcp and a
    // stdio client forwarding with --url work from the first call.
    if (config.signature_policy === "humans" && config.agent?.uri) await joinMcpAgent(coord, config);
    return;
  }
  const ws = coord.getWorkspace(config.workspace);
  const members = [...(config.humans ?? []).map((h) => ({ ...h, type: "human" })), ...(config.agent ? [{ ...config.agent, type: "agent" }] : [])];
  for (const m of members) {
    if (ws.members.has(m.uri)) continue;
    const r = send("participant.join", { from: m.uri, type: m.type, role: m.role ?? (m.type === "human" ? "reviewer" : "drafter"), display_name: m.display_name });
    if (r.error) throw new Error(`participant.join ${m.uri}: ${r.error.message}`);
  }
}

/**
 * A workspace keeps the signature policy it was created with, and one that
 * advertises security-signed/1.0 requires signatures whatever the option
 * says. Where the store holds a workspace made under other settings, the
 * configuration is made to say what the workspace does, so the console and
 * /api/config tell the desk and the agent how to call, and the console says
 * what changed.
 */
function settleSignatures(ws, config, log) {
  const required = !!config.require_signatures || hasProfile(ws.profiles, "security-signed");
  const policy = required ? (ws.signature_policy === "humans" ? "humans" : "all") : null;
  if (required === !!config.require_signatures && policy === config.signature_policy) return;
  log(`${ws.id} was created under other settings, and runs with ${SIGNING[policy]}. require_signatures in chap.config.json applies to a new store.`);
  config.require_signatures = required;
  config.signature_policy = policy;
}

/**
 * The public key of the agent's key file, the one CHAP_AGENT_KEY names or
 * keys/<agent>.jwk.json in the project, or null where there is none.
 */
async function agentPublicKey(config) {
  const path = process.env.CHAP_AGENT_KEY ?? keyPathFor(config.agent.uri, join(config.dir, "keys"));
  let text;
  try { text = await readFile(path, "utf8"); } catch (e) {
    if (e.code === "ENOENT") return null;
    throw e;
  }
  try { return publicJwkOf(JSON.parse(text)); } catch { throw new Error(`The agent's key file ${path} does not read as a JWK`); }
}

/**
 * Join the agent /mcp acts as, where the workspace does not hold it. Under
 * the humans policy the coordinator takes an unsigned call only from a
 * member that is not a person, and /mcp's calls are unsigned, so the agent
 * must be a member before its first one. It joins with the public key of
 * its key file when the project has one, so an agent that also signs, as the
 * code gate's does, keeps the key it signs with, and with none otherwise.
 */
async function joinMcpAgent(coord, config) {
  const ws = coord.getWorkspace(config.workspace);
  if (!ws || config.signature_policy !== "humans" || ws.members.has(config.agent.uri)) return;
  const key = await agentPublicKey(config);
  const { uri, role, display_name } = config.agent;
  coord.dispatch({ jsonrpc: "2.0", id: "mcp-join", method: "participant.join", params: {
    workspace: config.workspace, from: uri, type: "agent", role: role ?? "drafter",
    ...(display_name ? { display_name } : {}), ...(key ? { jwks: { keys: [key] } } : {}),
  } });
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
    desk_signing: config.desk_signing ?? "browser-key",
    chain_enabled: !!config.chain,
    persistent: config.store !== ":memory:",
    // Where this project lives, so a prompt the desk writes can name its commands.
    gate_dir: config.dir,
    // What the desk signs people in with; never the client secret.
    oidc: config.oidc ? {
      issuer: config.oidc.issuer, client_id: config.oidc.client_id,
      authorization_endpoint: config.oidc.discovery?.authorization_endpoint ?? null,
      development: config.oidc.discovery?.chap_development_issuer === true,
    } : null,
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

/**
 * A join under a name chap.config.json does not give. The server admits the
 * people, the agent and the trust policy's members it is configured with; a
 * participant.join for any other URI is refused before the coordinator sees
 * it, so a process that can reach /chap cannot add a "person" of its own
 * making and decide as them. Null for every other call.
 */
function unnamedJoin(envelope, config) {
  if (envelope?.method !== "participant.join") return null;
  const p = envelope.params;
  if (!p || typeof p !== "object" || p.workspace !== config.workspace) return null;
  const named = new Set([...(config.humans ?? []).map((h) => h.uri), config.agent?.uri, config.operator?.uri, ...(config.enrolled ?? [])].filter(Boolean));
  if (named.has(p.from)) return null;
  return { jsonrpc: "2.0", id: envelope.id ?? null, error: { code: -32011,
    message: `${String(p.from)} is not a member chap.config.json names; add them to humans, or agent, to let them join` } };
}

/** Bring the coordinator up to date with its store, before a read of its memory. */
function keepUp(coord, config) {
  coord.dispatch({ jsonrpc: "2.0", id: "api-read", method: "workspace.describe", params: { workspace: config.workspace } });
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
const API_PATHS = new Set(["/api/config", "/api/reviews", "/api/tasks", "/api/health", "/api/members", "/api/chain", "/api/chain/verify", "/api/oidc/token"]);

/**
 * Redeem a sign-in's code at the provider's token endpoint, for the desk.
 * The browser sends the code it was given and the PKCE verifier it kept,
 * and the server makes the request, with the client secret where there is
 * one, which the browser never sees, and over a connection the desk's
 * policy needs no exception for. Only the ID token goes back, checked as
 * the join will check it, with its nonce held to the one the desk asked
 * for, so the desk can say who signed in and why a token will not do.
 * Returns { status, body }.
 */
async function redeemCode(config, provider, { code, code_verifier, redirect_uri, nonce }) {
  const { issuer, client_id: clientId, client_secret: secret } = config.oidc;
  const form = new URLSearchParams({ grant_type: "authorization_code", code, redirect_uri, client_id: clientId, code_verifier });
  const headers = { "content-type": "application/x-www-form-urlencoded", accept: "application/json" };
  if (secret) {
    // HTTP Basic, which every provider takes (RFC 6749 2.3.1), unless the
    // provider lists only client_secret_post.
    const methods = provider.discovery.token_endpoint_auth_methods_supported ?? ["client_secret_basic"];
    if (!methods.includes("client_secret_basic") && methods.includes("client_secret_post")) form.set("client_secret", secret);
    else headers.authorization = `Basic ${Buffer.from(`${encodeURIComponent(clientId)}:${encodeURIComponent(secret)}`).toString("base64")}`;
  }
  let answer;
  try {
    const res = await fetch(provider.discovery.token_endpoint, { method: "POST", headers, body: form, signal: AbortSignal.timeout(10_000) });
    answer = await res.json().catch(() => ({}));
    if (!res.ok) return { status: 400, body: { error: `${issuer} refused the sign-in's code: ${answer.error ?? `HTTP ${res.status}`}${answer.error_description ? `, ${answer.error_description}` : ""}` } };
  } catch (e) {
    return { status: 502, body: { error: `${issuer}'s token endpoint could not be reached: ${e.cause?.code ?? e.message}` } };
  }
  if (typeof answer.id_token !== "string") return { status: 502, body: { error: `${issuer} gave no ID token for the code` } };
  const checked = provider.people(answer.id_token);
  if (!checked.claims) return { status: 403, body: { error: `The sign-in cannot join you: ${checked.reason}.`, ...(checked.subject ? { subject: checked.subject } : {}), ...(checked.email ? { email: checked.email } : {}) } };
  if (checked.claims.nonce !== nonce) return { status: 400, body: { error: "The ID token was issued for another sign-in; sign in again." } };
  return { status: 200, body: { id_token: answer.id_token, as: checked.claims.chap_participant_uri, subject: checked.claims.sub } };
}

/**
 * A read the coordinator answers from the workspace it holds: audit.read
 * or audit.verify_chain, run with the coordinator's own handlers. They
 * change nothing and are never recorded, so the read API asks them
 * in-process, as it reads the tasks, and a reader needs no signature: the
 * desk would otherwise sign each poll, and a key that asks for a gesture on
 * every signature would ask for one each time. Returns { result } or { error }.
 */
function coordinatorRead(coord, method, params) {
  return coord.handlers.get(method)(params);
}

/**
 * The workspace's members and the keys each signs with now: the keys it
 * joined or rotated to, less those revoked or rotated away. A member's
 * public keys are no secret, and the desk reads them here to know whether
 * the workspace holds its key for the reviewer, with no signed call.
 */
export function listMembers(coord, workspace) {
  const ws = coord.getWorkspace(workspace);
  if (!ws) return [];
  return [...ws.members.values()].map((m) => ({
    uri: m.uri, type: m.type, role: m.role, ...(m.display_name ? { display_name: m.display_name } : {}),
    keys: (m.keys ?? []).filter((k) => !k.revoked_at && !k.valid_until).map((k) => k.jwk),
  }));
}

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
  // The provider the desk signs people in with, read before the first request.
  const provider = config.oidc ? await oidcProvider(config) : null;
  if (config.mcp) {
    // Stateless: the MCP client keeps no session with this process. Both
    // protocol eras are answered, the handshake one and 2026-07-28.
    // The handler checks Host and Origin itself, against the loopback names
    // on the port a request came in on and the names given here: the ones
    // this server answers to besides those.
    const names = [...(config.allowed_hosts ?? []), ...(config.port ? [`${config.host}:${config.port}`] : [])];
    mcp = chapMcpHttpHandler(coord, { name: config.workspace, version: VERSION,
      participant: config.agent.uri, toolFilter: (name) => AGENT_MCP_TOOLS.has(name), maxBodyBytes: bodyLimit(config),
      allowedHosts: names.map((h) => h.toLowerCase()) });
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
        const stranger = unnamedJoin(envelope, config);
        if (stranger) return reply(res, 200, stranger);
        return reply(res, 200, coord.dispatch(envelope));
      }
      // The read API reads the coordinator's memory. A cheap call first lets
      // the coordinator take in what another process wrote to the store.
      if (path.startsWith("/api/") && path !== "/api/oidc/token") keepUp(coord, config);
      if (path === "/api/config") return reply(res, 200, publicConfig(config));
      if (path === "/api/oidc/token") {
        if (!provider) return reply(res, 404, { error: "no OIDC issuer is configured" });
        if (req.method !== "POST") return reply(res, 405, { error: "POST /api/oidc/token takes a sign-in's code" });
        if (!(req.headers["content-type"] ?? "").toLowerCase().startsWith("application/json")) return reply(res, 415, { error: "POST /api/oidc/token takes application/json" });
        let body;
        try { body = JSON.parse(await readBody(req, 64 * 1024)); } catch (e) {
          if (e instanceof BodyTooLarge) throw e;
          return reply(res, 400, { error: "the body is not JSON" });
        }
        const fields = ["code", "code_verifier", "redirect_uri", "nonce"];
        if (!fields.every((f) => typeof body?.[f] === "string" && body[f])) return reply(res, 400, { error: `${fields.join(", ")} are each a string` });
        const answer = await redeemCode(config, provider, body);
        return reply(res, answer.status, answer.body);
      }
      if (path === "/api/members") return reply(res, 200, { members: listMembers(coord, config.workspace) });
      if (path === "/api/chain") {
        const range = {};
        for (const name of ["from_seq", "to_seq"]) {
          const v = url.searchParams.get(name);
          if (v === null) continue;
          if (!/^\d+$/.test(v)) return reply(res, 400, { error: `${name} is a whole number` });
          range[name] = Number(v);
        }
        const r = coordinatorRead(coord, "audit.read", { workspace: config.workspace, range });
        return r.error ? reply(res, 400, { error: r.error.message }) : reply(res, 200, r.result);
      }
      if (path === "/api/chain/verify") return reply(res, 200, coordinatorRead(coord, "audit.verify_chain", { workspace: config.workspace }));
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
        await joinMcpAgent(coord, config);
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
  // On the loopback address a second server answers on ::1, and closes with this one.
  let ipv6 = null;
  server.on("close", () => ipv6?.close());
  return {
    config, coord, handler, server,
    async listen({ port = config.port, host = config.host, log = console.log } = {}) {
      if (LOOPBACK.has(host)) ipv6 = await listenOnLoopback(server, handler, port);
      else await listenOn(server, port, host).catch((e) => { throw listenError(e, port, host); });
      const base = deskBase(host, server.address().port);
      config.desk_url ??= `${base}/`;
      log(`CHAP ${config.workspace} on ${base}`);
      log(`  desk     ${base}/`);
      log(`  calls    POST ${base}/chap`);
      if (config.mcp) {
        log(`  mcp      ${base}/mcp, as ${config.agent.uri}`);
        if (config.signature_policy === "all") log(`           every call is signed here and /mcp's are not, so they are refused; "require_signatures": "humans" takes them`);
      }
      log(`  profiles ${config.profiles.join(", ")}`);
      log(`  store    ${config.store}${config.chain ? ", chain on" : ""}`);
      if (config.oidc) log(`  oidc     people sign in at ${config.oidc.issuer}, as client ${config.oidc.client_id}${config.oidc.discovery?.chap_development_issuer ? "; a development issuer, which authenticates nobody" : ""}`);
      log(`  signing  ${SIGNING[config.signature_policy]}`);
      if (config.require_signatures) log(`           ${DESK_SIGNING[config.desk_signing ?? "browser-key"]}`);
      if (config.notify) log(`  notify   ${[config.notify.console ? "console" : null, config.notify.webhook ? `webhook ${new URL(config.notify.webhook.url).origin}` : null].filter(Boolean).join(", ") || "nothing"}, linking ${config.desk_url}`);
      return { base };
    },
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

/** The loopback address by the names a host setting gives it. */
const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);
/** The addresses that listen on every interface, the loopback one among them. */
const WILDCARD = new Set(["0.0.0.0", "::", "[::]"]);

/**
 * Where the desk is opened, for a server listening on `host` and `port`.
 * The desk may sign with a passkey, and WebAuthn takes a domain name for
 * its relying party, never an IP address, so a server on this machine's
 * loopback address, or on every address, is reached at localhost; any
 * other host as it is given.
 */
export function deskBase(host, port) {
  if (LOOPBACK.has(host) || WILDCARD.has(host)) return `http://localhost:${port}`;
  return `http://${host.includes(":") && !host.startsWith("[") ? `[${host}]` : host}:${port}`;
}

/** Listen on one address; resolves once listening. */
function listenOn(server, port, host) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => { server.off("error", reject); resolve(); });
  });
}

/**
 * Listen on 127.0.0.1 and ::1 at one port, so localhost reaches the server
 * whichever of the two the name resolves to first, and never another
 * program listening on the other. `port` 0 takes a port free on both.
 * Returns the server on ::1, or null on a machine with no IPv6 loopback
 * address, where localhost can only be 127.0.0.1.
 */
async function listenOnLoopback(server, handler, port) {
  for (let attempt = 1; ; attempt++) {
    await listenOn(server, port, "127.0.0.1").catch((e) => { throw listenError(e, port, "127.0.0.1"); });
    const taken = server.address().port;
    const ipv6 = createServer(handler);
    try {
      await listenOn(ipv6, taken, "::1");
      return ipv6;
    } catch (e) {
      if (e.code === "EADDRNOTAVAIL" || e.code === "EAFNOSUPPORT") return null;
      await new Promise((resolve) => server.close(resolve));
      if (e.code === "EADDRINUSE" && port === 0 && attempt < 5) continue;
      if (e.code === "EADDRINUSE") throw new Error(`Port ${taken} on ::1 is in use, and localhost may reach that address first: another process answers there. Stop it, or give this one another port with PORT or "port" in chap.config.json.`);
      throw listenError(e, taken, "::1");
    }
  }
}

/** Why the server could not listen, in a line that says what to do. */
function listenError(err, port, host) {
  if (err.code === "EADDRINUSE") return new Error(`Port ${port} on ${host} is in use: another process answers there, a gate or desk started earlier perhaps. Stop it, or give this one another port with PORT or "port" in chap.config.json.`);
  if (err.code === "EACCES") return new Error(`Port ${port} on ${host} needs privileges this process does not have; give it a port above 1023 with PORT or "port" in chap.config.json.`);
  if (err.code === "EADDRNOTAVAIL") return new Error(`${host} is not an address of this machine; set CHAP_HOST, or "host" in chap.config.json.`);
  return err;
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
 * or from chap.config.json in `dir`. Returns { coord, server, base, close }.
 */
export async function serve({ dir = process.cwd(), configPath } = {}) {
  return start(await loadConfig(configPath ?? process.env.CHAP_CONFIG ?? join(dir, "chap.config.json")));
}

/**
 * Start the server for a config: the coordinator, the handler, and the port.
 * Returns { coord, server, base, close }; closing `server` closes the
 * server on ::1 with it.
 */
export async function start(config) {
  const chap = await createChapServer({ config });
  const { base } = await chap.listen();
  return { coord: chap.coord, server: chap.server, base, close: chap.close };
}
