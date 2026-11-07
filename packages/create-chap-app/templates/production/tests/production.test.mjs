// The production set in-process, on an in-memory store: signatures required,
// the chain on, and OIDC verified at join against the development issuer.
// The decisions here are scripted because this is a test; in the project the
// decision is made at the desk.
import { test, describe, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readdir, readFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash, generateKeyPairSync, randomBytes } from "node:crypto";
import { enrolmentCode, loadConfig as loadConfigFrom, makeCoordinator, makeServer } from "@brightbeamai/chap-server";
import { makeClient, generateSigner, signerFromJwk, thumbprint as jwkThumbprint } from "@brightbeamai/chap-server/client";
import { generateKeyFile, readKeyFile, kidFor, thumbprint } from "@brightbeamai/chap-server/keys";
import { startDevIssuer, signJwt } from "@brightbeamai/chap-server/dev-issuer";
import { makeOidcVerifier } from "@brightbeamai/chap-server/oidc";
import { runChecks } from "../doctor.mjs";
import { parseCsv, rowKey, scriptedDraft, buildPrompt, run as runAgent } from "../agent.mjs";

const projectDir = fileURLToPath(new URL("..", import.meta.url));
const loadConfig = () => loadConfigFrom(join(projectDir, "chap.config.json"));

async function startServer(config) {
  const coord = await makeCoordinator(config);
  const server = await makeServer(config, coord);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { coord, server, base: `http://127.0.0.1:${server.address().port}` };
}

async function post(base, envelope) {
  const res = await fetch(`${base}/chap`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(envelope) });
  return res.json();
}

let config, coord, server, base, keyDir, keyPath, agent, human, taskId;

before(async () => {
  config = await loadConfig();
  config.store = ":memory:";
  ({ coord, server, base } = await startServer(config));
  keyDir = await mkdtemp(join(tmpdir(), "chap-keys-"));
  ({ path: keyPath } = await generateKeyFile(config.agent.uri, keyDir));
  const signer = await signerFromJwk(config.agent.uri, await readKeyFile(config.agent.uri, keyPath));
  agent = makeClient({ url: `${base}/chap`, workspace: config.workspace, from: config.agent.uri, signer });
  // The person joins with the code the server printed, as the desk does
  // from the link it printed.
  human = makeClient({ url: `${base}/chap`, workspace: config.workspace, from: config.humans[0].uri, signer: await generateSigner(config.humans[0].uri), enrolment: enrolmentCode(config) });
});

after(async () => {
  await new Promise((r) => server.close(r));
});

test("the configuration asks for signatures, the chain and the production profiles", async () => {
  assert.equal(config.require_signatures, true);
  assert.equal(config.chain, true);
  for (const p of ["core/1.0", "review/1.0", "modes/1.0", "security-signed/1.0"]) assert.ok(config.profiles.includes(p), p);
  // No issuer is configured here, so identity-oidc/1.0 is left out and the
  // descriptor says the same: the coordinator refuses a workspace that
  // advertises it without a verifier.
  assert.ok(!config.profiles.includes("identity-oidc/1.0"));
  const cfg = await (await fetch(`${base}/api/config`)).json();
  assert.equal(cfg.require_signatures, true);
  assert.equal(cfg.signature_policy, "all", "every call is signed, the agent's among them");
  assert.equal(cfg.desk_signing, "auto", "the desk signs with a passkey where the browser has one");
  assert.equal(cfg.chain_enabled, true);
  assert.equal(cfg.oidc, null);
  assert.deepEqual(cfg.profiles, config.profiles);
});

test("npm run keys writes the private key once, readable by its owner only, named by its thumbprint", async () => {
  const mode = (await stat(keyPath)).mode & 0o777;
  assert.equal(mode, 0o600);
  const jwk = JSON.parse(await readFile(keyPath, "utf8"));
  assert.equal(jwk.kty, "OKP");
  assert.equal(jwk.crv, "Ed25519");
  assert.equal(typeof jwk.d, "string");
  assert.equal(jwk.kid, thumbprint(jwk));
  assert.notEqual(jwk.kid, kidFor(config.agent.uri), "a key's id comes from the key, and no longer from the URI");
  assert.equal(agent.signer.kid, jwk.kid);
  const again = await generateKeyFile(config.agent.uri, keyDir);
  assert.equal(again.created, false);
  assert.equal(again.publicJwk.x, jwk.x);
});

test("an unsigned decision in the reviewer's name is refused with -32070", async () => {
  const r = await post(base, { jsonrpc: "2.0", id: "u1", method: "decide.approve",
    params: { workspace: config.workspace, from: config.humans[0].uri, task_id: "tsk_x" } });
  assert.equal(r.error?.code, -32070);
});

test("the agent joins with its public key and its signed task.create is accepted", async () => {
  const joined = await agent.call("participant.join", { type: "agent", role: "drafter", jwks: { keys: [agent.signer.publicJwk] } });
  assert.equal(joined.joined, true);
  const created = await agent.call("task.create", { kind: "draft_message", assignee: config.agent.uri,
    input: { to: "a@example.com", subject: "Hello", brief: "Say hello." }, mode: "trial", review_required: true });
  assert.equal(created.state, "created");
  taskId = created.task_id;
  // Under modes/1.0 a trial task requires review, and a review needs a human
  // other than the producer. Before any human has joined the completion is
  // refused, which is what the agent waits on.
  await assert.rejects(agent.call("task.complete", { task_id: taskId, output: { body: "Hello" } }), (e) => e.code === -32011);
});

test("a human signer joins and decides through POST /chap, signed", async () => {
  await human.call("participant.join", { type: "human", role: "reviewer", display_name: config.humans[0].display_name, jwks: { keys: [human.signer.publicJwk] } });
  const done = await agent.call("task.complete", { task_id: taskId, output: { to: "a@example.com", subject: "Hello", body: "Hello there." } });
  assert.equal(done.state, "review_requested");
  const reviews = await (await fetch(`${base}/api/reviews?reviewer=${encodeURIComponent(config.humans[0].uri)}`)).json();
  assert.ok(reviews.reviews.some((r) => r.task_id === taskId));
  // The agent cannot approve its own work.
  await assert.rejects(agent.call("decide.approve", { task_id: taskId }), (e) => e.code === -32011);
  const decided = await human.call("decide.approve", { task_id: taskId, comment: "send it" });
  assert.equal(decided.state, "completed");
  const view = await (await fetch(`${base}/api/tasks/${taskId}`)).json();
  assert.equal(view.state, "completed");
  assert.equal(view.output.body, "Hello there.");
  assert.equal(view.review.decisions.at(-1).reviewer, config.humans[0].uri);
});

test("a tampered envelope is refused, and a signature binds the sender it was made for", async () => {
  const envelope = { jsonrpc: "2.0", id: "t1", method: "task.create",
    params: { workspace: config.workspace, from: config.agent.uri, kind: "draft_message", input: { n: 1 }, assignee: config.agent.uri } };
  const signed = await agent.signer.sign(envelope);
  const tampered = { ...signed, params: { ...signed.params, input: { n: 2 } } };
  const r = await post(base, tampered);
  assert.equal(r.error?.code, -32070);
  // The same key presented in the human's name: the kid is looked up on the member named by `from`.
  const asHuman = { ...signed, params: { ...signed.params, from: config.humans[0].uri } };
  const r2 = await post(base, asHuman);
  assert.equal(r2.error?.code, -32071, JSON.stringify(r2));
  // The untouched envelope still verifies.
  const r3 = await post(base, signed);
  assert.equal(r3.result?.state, "created");
});

test("audit.verify_chain verifies the chain", async () => {
  const v = await human.call("audit.verify_chain", {});
  assert.equal(v.status, "verified");
  assert.equal(v.ok, true);
  assert.ok(v.entries_checked >= 5);
  const d = await human.call("workspace.describe", {});
  assert.match(d.evidence_head, /^sha256:[0-9a-f]{64}$/);
  assert.ok(d.profiles.includes("security-signed/1.0"));
});

test("the doctor's checks pass against the in-process server without an issuer", async () => {
  const results = await runChecks({ url: `${base}/chap`, dir: projectDir, keyPath, storePath: ":memory:" });
  const byName = Object.fromEntries(results.map((r) => [r.name, r]));
  for (const name of ["health", "config", "unsigned", "describe", "chain"]) assert.equal(byName[name].status, "ok", `${name}: ${byName[name].detail}`);
  assert.equal(byName.store.status, "skip");
  assert.equal(byName.oidc.status, "skip");
});

describe("with an OIDC issuer", () => {
  let issuer, oconfig, ocoord, oserver, obase, reviewer;

  before(async () => {
    issuer = await startDevIssuer(0);
    oconfig = await loadConfig();
    oconfig.store = ":memory:";
    oconfig.workspace = `${oconfig.workspace}_oidc`;
    oconfig.oidc = { issuer: issuer.issuer, client_id: issuer.audience };
    ({ coord: ocoord, server: oserver, base: obase } = await startServer(oconfig));
    reviewer = makeClient({ url: `${obase}/chap`, workspace: oconfig.workspace, from: oconfig.humans[0].uri, signer: await generateSigner(oconfig.humans[0].uri) });
  });

  after(async () => {
    await new Promise((r) => oserver.close(r));
    await issuer.close();
  });

  /** An ID token for `who` with `nonce`, through the sign-in the desk makes: the issuer's code, redeemed by the server. */
  async function signIn(who, nonce) {
    const verifier = randomBytes(32).toString("base64url");
    const url = new URL(`${issuer.issuer}/authorize`);
    for (const [k, v] of Object.entries({ response_type: "code", client_id: issuer.audience, redirect_uri: `${obase}/`, scope: "openid", state: "s", nonce,
      code_challenge: createHash("sha256").update(verifier).digest("base64url"), code_challenge_method: "S256", login_hint: who })) url.searchParams.set(k, v);
    const code = new URL((await fetch(url, { redirect: "manual" })).headers.get("location")).searchParams.get("code");
    const res = await fetch(`${obase}/api/oidc/token`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ code, code_verifier: verifier, redirect_uri: `${obase}/`, nonce }) });
    return (await res.json()).id_token;
  }

  test("the workspace advertises identity-oidc/1.0 and /api/config names the issuer and the client the desk signs in as", async () => {
    const cfg = await (await fetch(`${obase}/api/config`)).json();
    assert.deepEqual(cfg.oidc, { issuer: issuer.issuer, client_id: issuer.audience, authorization_endpoint: `${issuer.issuer}/authorize`, development: true });
    assert.ok(cfg.profiles.includes("identity-oidc/1.0"));
    assert.ok(ocoord.getWorkspace(oconfig.workspace).profiles.includes("identity-oidc/1.0"));
  });

  test("a person's join with no token is refused with -32403, so nobody takes the reviewer's name first", async () => {
    const stranger = await generateSigner(reviewer.from);
    const r = await post(obase, { jsonrpc: "2.0", id: "t0", method: "participant.join", params: { workspace: oconfig.workspace, from: reviewer.from, type: "human", jwks: { keys: [stranger.publicJwk] } } });
    assert.equal(r.error?.code, -32403);
    assert.equal(ocoord.getWorkspace(oconfig.workspace).members.has(reviewer.from), false);
  });

  test("the desk's sign-in binds its key through the nonce, and the reviewer then signs with that key", async () => {
    const token = await signIn(reviewer.from, await jwkThumbprint(reviewer.signer.publicJwk));
    const joined = await reviewer.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [reviewer.signer.publicJwk] }, oidc_token: token });
    assert.equal(joined.joined, true);
    const created = await reviewer.call("task.create", { kind: "note", assignee: reviewer.from, input: { text: "signed with the bound key" } });
    assert.equal(created.state, "created");
    const me = (await reviewer.call("workspace.describe", {})).members.find((m) => m.uri === reviewer.from);
    assert.equal(me.oidc_sub, reviewer.from, "the development issuer's subject is the URI it signed in");
    assert.deepEqual(me.jwks.keys.map((k) => k.kid), [reviewer.signer.kid]);
    const entry = ocoord.getWorkspace(oconfig.workspace).audit.find((e) => e.envelope?.method === "participant.join" && e.envelope.params.from === reviewer.from);
    assert.equal(entry.envelope.params.oidc_token, `sha256:${createHash("sha256").update(token).digest("hex")}`, "the chain keeps the token's digest, never the token");
  });

  test("a join under a name chap.config.json does not give is refused at the server, and a token for another participant than the join's with -32404", async () => {
    const other = await generateSigner("human:someone-else@local");
    const unnamed = issuer.mintToken({ sub: "user-other", uri: "human:someone-else@local" });
    const r = await post(obase, { jsonrpc: "2.0", id: "o1", method: "participant.join",
      params: { workspace: oconfig.workspace, from: "human:someone-else@local", type: "human", jwks: { keys: [other.publicJwk] }, oidc_token: unnamed } });
    assert.equal(r.error?.code, -32011);
    const forReviewer = issuer.mintToken({ sub: "user-other", uri: reviewer.from });
    // A join under the reviewer's name with a token for a different subject is refused the same way.
    const r3 = await post(obase, { jsonrpc: "2.0", id: "o3", method: "participant.join",
      params: { workspace: oconfig.workspace, from: reviewer.from, type: "human", oidc_token: forReviewer } });
    assert.equal(r3.error?.code, -32404);
  });

  test("a garbage token, a token from an unknown key, an expired token and one for another audience are refused with -32403", async () => {
    const join = (oidc_token, id) => post(obase, { jsonrpc: "2.0", id, method: "participant.join",
      params: { workspace: oconfig.workspace, from: reviewer.from, type: "human", oidc_token } });
    const keysBefore = ocoord.getWorkspace(oconfig.workspace).members.get(reviewer.from)?.keys.length;
    assert.equal((await join("garbage", "g1")).error?.code, -32403);
    assert.equal((await join("not.a.token", "g2")).error?.code, -32403);
    const rogue = generateKeyPairSync("ec", { namedCurve: "P-256" });
    const forged = signJwt({ privateKey: rogue.privateKey, kid: "rogue", payload: { iss: issuer.issuer, sub: "x", aud: issuer.audience, exp: Math.floor(Date.now() / 1000) + 600 } });
    assert.equal((await join(forged, "g3")).error?.code, -32403);
    const expired = issuer.mintToken({ sub: "x", uri: reviewer.from, expires_in_sec: -3600 });
    assert.equal((await join(expired, "g4")).error?.code, -32403);
    const elsewhere = issuer.mintToken({ sub: "x", uri: reviewer.from, audience: "another-service" });
    assert.equal((await join(elsewhere, "g5")).error?.code, -32403);
    assert.equal(ocoord.getWorkspace(oconfig.workspace).members.get(reviewer.from)?.keys.length, keysBefore, "no refused join changed the reviewer's keys");
  });

  test("the verifier reads the issuer's discovery document and keys, checks signature, issuer, audience and time, and refetches the keys once on an unknown kid", async () => {
    let fetches = 0;
    const counting = (url, init) => { fetches++; return fetch(url, init); };
    const verify = await makeOidcVerifier({ issuer: issuer.issuer, audience: issuer.audience, fetchImpl: counting, refresh_min_ms: 0 });
    assert.equal(fetches, 2, "the discovery document, then the keys");
    const key = await generateSigner("human:claims@local");
    const good = verify(issuer.mintToken({ sub: "user-1", uri: "human:claims@local", cnfJwk: key.publicJwk, auth_time: 1747476000, acr: "mfa", extra: { nonce: "n-1" } }));
    assert.equal(good.sub, "user-1");
    assert.equal(good.iss, issuer.issuer);
    assert.equal(good.auth_time, 1747476000);
    assert.equal(good.acr, "mfa");
    assert.equal(good.nonce, "n-1");
    assert.equal(good.chap_participant_uri, "human:claims@local");
    assert.deepEqual(good.cnf.jwk, key.publicJwk);

    const payload = { iss: issuer.issuer, sub: "user-1", aud: issuer.audience, exp: Math.floor(Date.now() / 1000) + 600 };
    const rogue = generateKeyPairSync("ec", { namedCurve: "P-256" });
    assert.equal(verify(signJwt({ privateKey: rogue.privateKey, kid: "unknown-kid", payload })), null);
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(fetches, 3, "an unknown kid fetched the keys again");
    assert.equal(verify(signJwt({ privateKey: rogue.privateKey, kid: issuer.kid, payload })), null, "a forged signature under the right kid");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", extra: { iss: "https://elsewhere.example" } })), null, "a wrong issuer");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", audience: "someone-else" })), null, "a wrong audience");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", expires_in_sec: -120 })), null, "an expired token");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", extra: { nbf: Math.floor(Date.now() / 1000) + 600 } })), null, "a token not yet valid");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", extra: { sub: "" } })), null, "an empty subject");
    await assert.rejects(makeOidcVerifier({ issuer: issuer.issuer, audience: null }), /audience, the client id tokens are issued to, is required/, "an issuer without an audience is refused");
  });

  test("the doctor's checks pass against the in-process server with the issuer, the nonce check included", async () => {
    const results = await runChecks({ url: `${obase}/chap`, dir: projectDir, keyPath, storePath: ":memory:" });
    const byName = Object.fromEntries(results.map((r) => [r.name, r]));
    for (const name of ["health", "config", "unsigned", "describe", "chain", "oidc", "nonce"]) assert.equal(byName[name].status, "ok", `${name}: ${byName[name].detail}`);
    assert.match(byName.oidc.detail, /a person's join with no token, and one with a garbage token, are refused with -32403/);
    assert.match(byName.nonce.detail, /whose nonce names another key binds nothing: the join is refused with -32404/);
    assert.equal(byName.store.status, "skip");
    assert.equal(results.filter((r) => r.status === "FAIL").length, 0);
  });
});

test("the doctor fails a server that takes a person's join with no token, and skips the nonce check before the reviewer has signed in", async () => {
  // A verifier that takes any token, on a workspace that does not ask for one, stands for the 0.3.4 server.
  const issuer = await startDevIssuer(0);
  const lax = await loadConfig();
  lax.store = ":memory:";
  lax.workspace = `${lax.workspace}_lax`;
  lax.oidc = { issuer: issuer.issuer, client_id: issuer.audience };
  const coord = await makeCoordinator(lax, { verifyOidcToken: () => ({ sub: "anyone" }) });
  coord.getWorkspace(lax.workspace).profiles = coord.getWorkspace(lax.workspace).profiles.filter((p) => !p.startsWith("identity-oidc"));
  const server = await makeServer(lax, coord);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  try {
    const results = await runChecks({ url: `http://127.0.0.1:${server.address().port}/chap`, dir: projectDir, keyPath, storePath: ":memory:" });
    const byName = Object.fromEntries(results.map((r) => [r.name, r]));
    assert.equal(byName.oidc.status, "FAIL", byName.oidc.detail);
    assert.match(byName.oidc.detail, /expected -32403 for both, got (?!-32403 with no token and -32403)/);
    assert.equal(byName.nonce.status, "skip");
    assert.match(byName.nonce.detail, /has not signed in at the desk yet/);
  } finally {
    await new Promise((r) => server.close(r));
    await issuer.close();
  }
});

/** Poll until `read` gives something true, and give that back. */
async function until(read, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await read();
    if (value) return value;
    if (Date.now() > deadline) throw new Error("timed out waiting");
    await new Promise((r) => setTimeout(r, 50));
  }
}

describe("the agent's loop", () => {
  let lconfig, lserver, lbase, lhuman, probe, outbox;
  const reviews = async () => (await (await fetch(`${lbase}/api/reviews?reviewer=${encodeURIComponent(lhuman.from)}`)).json()).reviews;
  const reviewOf = async (taskId, holding) => (await reviews()).find((r) => r.task_id === taskId && (!holding || r.artefact.body.includes(holding)));

  before(async () => {
    // A coordinator of its own, so the loop's tasks stand alone.
    lconfig = await loadConfig();
    lconfig.store = ":memory:";
    ({ server: lserver, base: lbase } = await startServer(lconfig));
    lhuman = makeClient({ url: `${lbase}/chap`, workspace: lconfig.workspace, from: lconfig.humans[0].uri, signer: await generateSigner(lconfig.humans[0].uri), enrolment: enrolmentCode(lconfig) });
    // A signed reader with the agent's key, for the chain checks.
    probe = makeClient({ url: `${lbase}/chap`, workspace: lconfig.workspace, from: lconfig.agent.uri, signer: agent.signer });
    outbox = await mkdtemp(join(tmpdir(), "chap-outbox-"));
  });

  after(async () => {
    await new Promise((r) => lserver.close(r));
  });

  test("waits for a reviewer on a read, submits once one has joined, drafts again after a revision, and writes only what was approved", async () => {
    const lines = [];
    const running = runAgent({ once: true, dir: projectDir, url: `${lbase}/chap`, keyPath, outbox, log: (l) => lines.push(l), pollMs: 50 });

    // No human has joined, so the review cannot open: the agent says so once
    // and waits on workspace.describe, which records nothing.
    await until(() => lines.some((l) => l.includes("no reviewer has joined")));
    await new Promise((r) => setTimeout(r, 200));
    const refused = await probe.call("audit.read", { filter: { outcome: "refused" } });
    assert.deepEqual(refused.entries, []);
    assert.equal(lines.filter((l) => l.includes("no reviewer has joined")).length, 3, "said once per task, not once per pass");

    await lhuman.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [lhuman.signer.publicJwk] } });
    const open = await until(async () => { const r = await reviews(); return r.length === 3 ? r : null; });
    const rows = parseCsv(await readFile(join(projectDir, "messages.csv"), "utf8"));
    const [first, second, third] = rows.map((row) => open.find((r) => r.input.subject === row.subject));
    assert.ok(first && second && third);
    assert.equal(first.artefact.body, scriptedDraft(buildPrompt(rows[0])));

    // Approve one as written.
    assert.equal((await lhuman.call("decide.approve", { task_id: first.task_id, comment: "send it" })).state, "completed");

    // Reject one asking for a revision: the agent drafts again with the
    // note, the review opens again on the new draft, and that one is overridden.
    const rejected = await lhuman.call("decide.reject", { task_id: second.task_id, comment: "add the price", request_revision: true });
    assert.equal(rejected.state, "in_progress");
    const revised = await until(() => reviewOf(second.task_id, "add the price"));
    // The new draft opens a new round with no decisions, so the rejection
    // counts only for the draft it was made on; the task's log keeps it.
    assert.deepEqual(revised.decisions, []);
    const history = await (await fetch(`${lbase}/api/tasks/${second.task_id}`)).json();
    assert.equal(history.decision_log.at(-1).kind, "reject");
    const overridden = await lhuman.call("decide.override", { task_id: second.task_id, rationale: "shorter", diff: [{ op: "replace", path: "/body", value: "Edited body" }], intent_preserved: true });
    assert.equal(overridden.state, "completed");

    // Reject one outright.
    assert.equal((await lhuman.call("decide.reject", { task_id: third.task_id, comment: "not this week" })).state, "declined");

    const outcomes = await running;
    assert.deepEqual(outcomes, { [first.task_id]: "approve", [second.task_id]: "override", [third.task_id]: "reject" });
    assert.ok(lines.some((l) => l.includes("drafted again, after the reviewer's note")));

    const files = (await readdir(outbox)).sort();
    assert.deepEqual(files, [`${first.task_id}.json`, `${second.task_id}.json`].sort());
    const sent = JSON.parse(await readFile(join(outbox, `${first.task_id}.json`), "utf8"));
    assert.deepEqual(sent.message, first.artefact);
    assert.equal(sent.decision.reviewer, lhuman.from);
    const edited = JSON.parse(await readFile(join(outbox, `${second.task_id}.json`), "utf8"));
    assert.deepEqual(edited.message, { ...revised.artefact, body: "Edited body" });
    assert.equal(edited.decision.kind, "override");
  });

  test("a restarted agent finds its tasks by their idempotency keys and opens no duplicates", async () => {
    const before = (await (await fetch(`${lbase}/api/health`)).json()).tasks;
    const lines = [];
    const outcomes = await runAgent({ once: true, dir: projectDir, url: `${lbase}/chap`, keyPath, outbox, log: (l) => lines.push(l), pollMs: 50 });
    assert.deepEqual(Object.values(outcomes).sort(), ["approve", "override", "reject"]);
    assert.equal(lines.filter((l) => l.includes("decided earlier")).length, 2);
    assert.equal((await (await fetch(`${lbase}/api/health`)).json()).tasks, before);
  });
});

test("the agent reads messages.csv, keys each row and drafts without a model", async () => {
  const rows = parseCsv(await readFile(join(projectDir, "messages.csv"), "utf8"));
  assert.ok(rows.length >= 1);
  for (const row of rows) {
    assert.ok(row.to && row.subject && row.brief, JSON.stringify(row));
    assert.match(rowKey(row), /^msg-[0-9a-f]{24}$/);
  }
  assert.equal(rowKey(rows[0]), rowKey({ ...rows[0] }), "the same row keeps the same task");
  const body = scriptedDraft(buildPrompt(rows[0]));
  assert.match(body, /^Dear \w+,/);
  assert.ok(body.includes(rows[0].brief));
  assert.ok(scriptedDraft(buildPrompt(rows[0], "shorter please")).includes("shorter please"));
  assert.deepEqual(parseCsv('a,b\n"x, y","say ""hi"""\n'), [{ a: "x, y", b: 'say "hi"' }]);
});

test("through a symbolic link to the project, doctor runs its checks and fails when nothing answers", async () => {
  const { symlink } = await import("node:fs/promises");
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const link = join(await mkdtemp(join(tmpdir(), "chap-link-")), "project");
  await symlink(projectDir, link);
  await assert.rejects(promisify(execFile)(process.execPath, [join(link, "doctor.mjs")], { env: { ...process.env, CHAP_URL: "http://127.0.0.1:1/chap" } }),
    (e) => e.code === 1 && /FAIL health/.test(e.stdout));
});

test("server.mjs, agent.mjs, doctor.mjs and diff-profiles.mjs each print their help with --help", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  for (const [script, pattern] of [["server.mjs", /npm start/], ["agent.mjs", /--once/], ["doctor.mjs", /one line per check/], ["diff-profiles.mjs", /--against c,d/]]) {
    const { stdout } = await promisify(execFile)(process.execPath, [join(projectDir, script), "--help"], { timeout: 10000 });
    assert.match(stdout, pattern, script);
  }
});
