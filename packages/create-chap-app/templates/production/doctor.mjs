// doctor: checks a running coordinator against what this project promises.
//
// It starts nothing. It connects to CHAP_URL (default
// http://127.0.0.1:8790/chap), reads chap.config.json and the agent's key
// file, and prints one line per check, ok or FAIL, with the exit code
// saying whether every check passed. The checks:
//
//   health     the server answers GET /api/health
//   config     /api/config says signatures are required, the chain is on, and the
//              profiles include security-signed/1.0, and identity-oidc/1.0 when an issuer is set
//   unsigned   an unsigned task.create as the agent is refused with -32070
//   describe   workspace.describe, signed as the agent, advertises the profiles the
//              coordinator enforces and publishes a chain head
//   chain      audit.verify_chain answers status verified with ok true
//   store      the coordinator reports a persistent store, and the store file
//              exists and changes after a call (skipped when CHAP_DB_PATH is :memory:)
//   oidc       with an issuer configured, a join with a garbage token is refused with -32403
//
// The signed checks join the agent first with its public key, which is
// accepted unsigned and changes nothing for a member already on record. The
// unsigned probe and the garbage token are refused calls, and a refused call
// is recorded on the chain, so each run leaves those refusals on it.
// runChecks is exported, so the tests run the same checks in-process.

import { readFile, stat } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { makeClient, signerFromJwk } from "./desk/chap-client.mjs";
import { keyPathFor, readKeyFile } from "./keys.mjs";

const here = dirname(fileURLToPath(import.meta.url));

const PROBE_URI = "human:doctor-probe@local";

/**
 * Run every check. Returns [{ name, status: "ok" | "FAIL" | "skip", detail }].
 * `dir` is the project directory; `url` is where POST /chap answers.
 */
export async function runChecks({ url = process.env.CHAP_URL ?? "http://127.0.0.1:8790/chap", dir = here, keyPath, storePath } = {}) {
  const base = url.replace(/\/chap\/?$/, "");
  const results = [];
  const record = (name, status, detail) => { results.push({ name, status, detail }); return status === "ok"; };
  const config = JSON.parse(await readFile(join(dir, "chap.config.json"), "utf8"));
  const agentUri = config.agent?.uri;
  const store = storePath ?? process.env.CHAP_DB_PATH ?? config.store ?? "./data/chap.db";
  const storeFile = store === ":memory:" ? null : (isAbsolute(store) ? store : resolve(dir, store));

  // health
  let health;
  try {
    const res = await fetch(`${base}/api/health`);
    health = await res.json();
    if (!res.ok || health.ok !== true) throw new Error(`answered ${res.status}`);
    record("health", "ok", `${base}/api/health answers; ${health.members} members, ${health.tasks} tasks, ${health.audit} chain entries`);
  } catch (e) {
    record("health", "FAIL", `${base}/api/health: ${e instanceof Error ? e.message : String(e)}. Is the coordinator running?`);
    return results;
  }

  // config
  const cfg = await (await fetch(`${base}/api/config`)).json();
  const oidcOn = !!cfg.oidc?.issuer;
  {
    const problems = [];
    if (cfg.require_signatures !== true) problems.push("require_signatures is not true");
    if (cfg.chain_enabled !== true) problems.push("chain_enabled is not true");
    if (!has(cfg.profiles, "security-signed")) problems.push("profiles lack security-signed/1.0");
    if (oidcOn && !has(cfg.profiles, "identity-oidc")) problems.push("an issuer is set and profiles lack identity-oidc/1.0");
    record("config", problems.length ? "FAIL" : "ok",
      problems.length ? problems.join("; ") : `signatures required, chain on, profiles ${cfg.profiles.join(", ")}${oidcOn ? `, oidc ${cfg.oidc.issuer}` : ", no oidc issuer"}`);
  }

  // unsigned
  {
    const r = await post(`${base}/chap`, { jsonrpc: "2.0", id: "doctor-unsigned", method: "task.create",
      params: { workspace: cfg.workspace, from: agentUri, kind: "doctor", input: {}, assignee: agentUri } });
    const code = r.error?.code;
    record("unsigned", code === -32070 ? "ok" : "FAIL",
      code === -32070 ? `an unsigned task.create as ${agentUri} is refused with -32070` : `expected -32070, got ${r.error ? `${code} ${r.error.message}` : "an accepted call"}`);
  }

  // the agent's signer, for the signed checks; the join is the call the store check watches
  let client = null;
  let joined = false;
  const before = storeFile ? await snapshot(storeFile) : null;
  const path = keyPath ?? process.env.CHAP_AGENT_KEY ?? keyPathFor(agentUri, join(dir, "keys"));
  try {
    const signer = await signerFromJwk(agentUri, await readKeyFile(agentUri, path));
    client = makeClient({ url: `${base}/chap`, workspace: cfg.workspace, from: agentUri, signer });
    await client.call("participant.join", { type: "agent", role: config.agent.role ?? "drafter", display_name: config.agent.display_name, jwks: { keys: [signer.publicJwk] } });
    joined = true;
  } catch (e) {
    record("describe", "FAIL", `no signer for ${agentUri}: ${e instanceof Error ? e.message : String(e)}`);
    record("chain", "FAIL", "needs the signed describe above");
  }

  // describe
  if (client) {
    try {
      const d = await client.call("workspace.describe", {});
      const problems = [];
      if (!has(d.profiles, "security-signed")) problems.push("the descriptor lacks security-signed/1.0");
      if (oidcOn && !has(d.profiles, "identity-oidc")) problems.push("an issuer is set and the descriptor lacks identity-oidc/1.0");
      if (!oidcOn && has(d.profiles, "identity-oidc")) problems.push("the descriptor advertises identity-oidc/1.0 and no issuer is set");
      if (typeof d.evidence_head !== "string") problems.push("the descriptor publishes no evidence_head, so the chain is off for this workspace");
      record("describe", problems.length ? "FAIL" : "ok",
        problems.length ? problems.join("; ") : `signed as ${agentUri} with key ${client.signer.kid}; the workspace advertises ${d.profiles.join(", ")}; head ${d.evidence_head.slice(0, 23)}...`);
    } catch (e) {
      record("describe", "FAIL", `workspace.describe signed as ${agentUri}: ${e.message}${e.code === -32071 ? ". The key on record differs from the key file: rotate with participant.rotate_key, or join under a new URI" : ""}`);
    }

    // chain
    try {
      const v = await client.call("audit.verify_chain", {});
      const good = v.status === "verified" && v.ok === true;
      record("chain", good ? "ok" : "FAIL", good ? `audit.verify_chain: verified, ${v.entries_checked} entries checked` : `audit.verify_chain: status ${v.status}, ok ${v.ok}${v.reason ? `, ${v.reason}` : ""}`);
    } catch (e) {
      record("chain", "FAIL", `audit.verify_chain: ${e.message}`);
    }
  }

  // store
  if (!storeFile) {
    record("store", "skip", "CHAP_DB_PATH is :memory:, so nothing persists; set a path to check the store");
  } else if (cfg.persistent === false) {
    record("store", "FAIL", "the coordinator reports an in-memory store, so nothing persists; start it with CHAP_DB_PATH set to a file");
  } else if (!before) {
    record("store", "FAIL", `${storeFile} does not exist. Is the coordinator writing to this path, and is this run reading the same one?`);
  } else if (!joined) {
    record("store", "FAIL", "no call was made, so the store could not be watched; see the describe check");
  } else {
    const after = await snapshot(storeFile);
    const changed = after && JSON.stringify(after) !== JSON.stringify(before);
    record("store", changed ? "ok" : "FAIL", changed
      ? `${storeFile} exists and changed after a call (${before.bytes} to ${after.bytes} bytes, counting the write-ahead log)`
      : `${storeFile} did not change after a call`);
  }

  // oidc
  if (!oidcOn) {
    record("oidc", "skip", "no issuer configured; set OIDC_ISSUER to verify tokens at participant.join");
  } else {
    const r = await post(`${base}/chap`, { jsonrpc: "2.0", id: "doctor-oidc", method: "participant.join",
      params: { workspace: cfg.workspace, from: PROBE_URI, type: "human", oidc_token: "not.a.token" } });
    const code = r.error?.code;
    record("oidc", code === -32403 ? "ok" : "FAIL",
      code === -32403 ? `a join with a garbage token is refused with -32403 (issuer ${cfg.oidc.issuer})` : `expected -32403, got ${r.error ? `${code} ${r.error.message}` : "an accepted join"}`);
  }

  return results;
}

function has(profiles, name) {
  return Array.isArray(profiles) && profiles.some((p) => p === name || p.startsWith(name + "/"));
}

async function post(url, body) {
  const res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return res.json();
}

/** Size and modification time of the store and its write-ahead log, or null when the store is missing. */
async function snapshot(file) {
  try {
    const main = await stat(file);
    let bytes = main.size, mtime = main.mtimeMs;
    try {
      const wal = await stat(`${file}-wal`);
      bytes += wal.size;
      mtime = Math.max(mtime, wal.mtimeMs);
    } catch { /* no write-ahead log yet */ }
    return { bytes, mtime };
  } catch {
    return null;
  }
}

export function render(results) {
  const width = Math.max(...results.map((r) => r.name.length));
  return results.map((r) => `${r.status.padEnd(4)} ${r.name.padEnd(width)}  ${r.detail}`).join("\n");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const results = await runChecks();
  console.log(render(results));
  const failed = results.filter((r) => r.status === "FAIL").length;
  console.log(failed ? `${failed} check${failed === 1 ? "" : "s"} failed` : "every check passed");
  process.exit(failed ? 1 : 0);
}
