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
//   unsigned   an unsigned decision as the reviewer is refused with -32070
//   describe   workspace.describe, signed as the agent, advertises the profiles the
//              coordinator enforces and publishes a chain head
//   chain      audit.verify_chain answers status verified with ok true
//   store      the coordinator reports a persistent store, and the store file
//              exists and changes after a call (skipped when CHAP_DB_PATH is :memory:)
//   oidc       with an issuer configured, a person's join with no token, and one with a
//              garbage token, are each refused with -32403
//   nonce      with the development issuer, which hands a token to anyone, a token for the
//              reviewer whose nonce names another key than the join brings binds nothing:
//              the join is refused with -32011 and the reviewer's keys stay as they were.
//              Skipped before the reviewer has signed in at the desk, and with a provider
//              that asks a person to sign in, which doctor cannot do.
//
// The signed checks join the agent first with its public key, which is
// accepted unsigned and changes nothing for a member already on record. The
// unsigned probe, the joins with no token or a garbage one, and the nonce
// probe are refused calls, and a refused call is recorded on the chain, so
// each run leaves those refusals on it, a token as its digest.
// runChecks is exported, so the tests run the same checks in-process.

import { createHash, randomBytes } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { isMain, storePath as resolveStore, usage, wantsHelp } from "@brightbeamai/chap-server";
import { generateSigner, makeClient, signerFromJwk, thumbprint } from "@brightbeamai/chap-server/client";
import { keyPathFor, readKeyFile } from "@brightbeamai/chap-server/keys";

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
  const store = resolveStore(storePath ?? process.env.CHAP_DB_PATH ?? config.store ?? "./data/chap.db", dir);
  const storeFile = store === ":memory:" ? null : store;

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

  // unsigned: a decision in the reviewer's name, with no signature. Under
  // either signature policy a person's call must be signed.
  {
    const reviewer = config.humans?.[0]?.uri;
    const r = await post(`${base}/chap`, { jsonrpc: "2.0", id: "doctor-unsigned", method: "decide.approve",
      params: { workspace: cfg.workspace, from: reviewer, task_id: "tsk_doctor" } });
    const code = r.error?.code;
    record("unsigned", code === -32070 ? "ok" : "FAIL",
      code === -32070 ? `an unsigned decide.approve as ${reviewer} is refused with -32070` : `expected -32070, got ${r.error ? `${code} ${r.error.message}` : "an accepted call"}`);
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

  // oidc: a person joins with a token from the issuer, or not at all
  if (!oidcOn) {
    record("oidc", "skip", "no issuer configured; set OIDC_ISSUER and OIDC_CLIENT_ID to sign people in and verify their tokens at participant.join");
    record("nonce", "skip", "no issuer configured");
  } else {
    // The probe joins as the configured reviewer: the server admits only the
    // people chap.config.json names, so a join under any other name is
    // refused before the token is looked at.
    const probe = config.humans?.[0]?.uri ?? PROBE_URI;
    const joinWith = (id, extra) => post(`${base}/chap`, { jsonrpc: "2.0", id, method: "participant.join", params: { workspace: cfg.workspace, from: probe, type: "human", ...extra } });
    const bare = (await joinWith("doctor-oidc-none", {})).error?.code;
    const garbage = (await joinWith("doctor-oidc-garbage", { oidc_token: "not.a.token" })).error?.code;
    const good = bare === -32403 && garbage === -32403;
    record("oidc", good ? "ok" : "FAIL", good ? `a person's join with no token, and one with a garbage token, are refused with -32403 (issuer ${cfg.oidc.issuer})`
      : `expected -32403 for both, got ${bare ?? "an accepted join"} with no token and ${garbage ?? "an accepted join"} with a garbage one`);
    await nonceCheck({ cfg, config, base, record });
  }

  return results;
}

/**
 * A token whose nonce names another key binds nothing: with the reviewer
 * on record, a join under their name that brings a key of its own, with a
 * token for them whose nonce is another key's thumbprint, is refused with
 * -32404, and their keys stay as they were. The token comes from the
 * development issuer, which hands one to anyone through the sign-in the
 * desk makes; a real provider asks a person to sign in, which doctor does
 * not do.
 */
async function nonceCheck({ cfg, config, base, record }) {
  const reviewer = config.humans?.[0]?.uri;
  if (!cfg.oidc.development) return record("nonce", "skip", `a token needs a person to sign in at ${cfg.oidc.issuer}; the template's tests check this against the development issuer`);
  const keysOf = async () => (await (await fetch(`${base}/api/members`)).json()).members.find((m) => m.uri === reviewer)?.keys.map((k) => k.kid);
  const before = await keysOf();
  if (!before) return record("nonce", "skip", `${reviewer} has not signed in at the desk yet; sign in there, then run doctor again`);
  const [named, brought] = [await generateSigner(reviewer), await generateSigner(reviewer)];
  let token;
  try { token = await devToken(cfg, base, reviewer, await thumbprint(named.publicJwk)); } catch (e) {
    return record("nonce", "FAIL", `no token from the development issuer: ${e.message}`);
  }
  const r = await post(`${base}/chap`, { jsonrpc: "2.0", id: "doctor-nonce", method: "participant.join",
    params: { workspace: cfg.workspace, from: reviewer, type: "human", jwks: { keys: [brought.publicJwk] }, oidc_token: token } });
  const after = await keysOf();
  const held = JSON.stringify(after) === JSON.stringify(before);
  record("nonce", r.error?.code === -32404 && held ? "ok" : "FAIL", r.error?.code === -32404 && held
    ? `a token for ${reviewer} whose nonce names another key binds nothing: the join is refused with -32404, and ${reviewer} keeps ${before.length} key${before.length === 1 ? "" : "s"}`
    : `expected -32404 and ${reviewer}'s keys as they were; got ${r.error ? `${r.error.code} ${r.error.message}` : "an accepted join"}, keys ${JSON.stringify(after)}`);
}

/** An ID token for `who` with `nonce`, through the sign-in the desk makes: the development issuer's code, redeemed by the server. */
async function devToken(cfg, base, who, nonce) {
  const verifier = randomBytes(32).toString("base64url");
  const url = new URL(cfg.oidc.authorization_endpoint);
  const redirect = `${base}/`;
  for (const [k, v] of Object.entries({ response_type: "code", client_id: cfg.oidc.client_id, redirect_uri: redirect, scope: "openid", state: "doctor", nonce,
    code_challenge: createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256", login_hint: who })) url.searchParams.set(k, v);
  const sent = await fetch(url, { redirect: "manual" });
  const code = sent.headers.get("location") ? new URL(sent.headers.get("location")).searchParams.get("code") : null;
  if (!code) throw new Error(`${url.origin} gave no code (${sent.status})`);
  const res = await fetch(`${base}/api/oidc/token`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ code, code_verifier: verifier, redirect_uri: redirect, nonce }) });
  const body = await res.json();
  if (!res.ok) throw new Error(body.error ?? `the server answered ${res.status}`);
  return body.id_token;
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

if (isMain(import.meta.url) && wantsHelp()) {
  console.log(usage(import.meta.url));
} else if (isMain(import.meta.url)) {
  const results = await runChecks();
  console.log(render(results));
  const failed = results.filter((r) => r.status === "FAIL").length;
  console.log(failed ? `${failed} check${failed === 1 ? "" : "s"} failed` : "every check passed");
  process.exit(failed ? 1 : 0);
}
