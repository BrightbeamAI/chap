// The production set in-process, on an in-memory store: signatures required,
// the chain on, and OIDC verified at join against the development issuer.
// The decisions here are scripted because this is a test; in the project the
// decision is made at the desk.
import { test, describe, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { generateKeyPairSync } from "node:crypto";
import { loadConfig, makeCoordinator, makeServer } from "../server.mjs";
import { makeClient, generateSigner, signerFromJwk } from "../desk/chap-client.mjs";
import { generateKeyFile, readKeyFile, kidFor } from "../keys.mjs";
import { startDevIssuer, signJwt } from "../lib/dev-issuer.mjs";
import { makeOidcVerifier } from "../lib/oidc.mjs";
import { runChecks } from "../doctor.mjs";
import { parseCsv, rowKey, scriptedDraft, buildPrompt } from "../agent.mjs";

const projectDir = fileURLToPath(new URL("..", import.meta.url));

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
  human = makeClient({ url: `${base}/chap`, workspace: config.workspace, from: config.humans[0].uri, signer: await generateSigner(config.humans[0].uri) });
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
  assert.equal(cfg.chain_enabled, true);
  assert.equal(cfg.oidc, null);
  assert.deepEqual(cfg.profiles, config.profiles);
});

test("keys.mjs writes the private key once, readable by its owner only, with the desk's kid", async () => {
  const mode = (await stat(keyPath)).mode & 0o777;
  assert.equal(mode, 0o600);
  const jwk = JSON.parse(await readFile(keyPath, "utf8"));
  assert.equal(jwk.kty, "OKP");
  assert.equal(jwk.crv, "Ed25519");
  assert.equal(typeof jwk.d, "string");
  assert.equal(jwk.kid, kidFor(config.agent.uri));
  assert.equal(agent.signer.kid, jwk.kid);
  const again = await generateKeyFile(config.agent.uri, keyDir);
  assert.equal(again.created, false);
  assert.equal(again.publicJwk.x, jwk.x);
});

test("an unsigned call is refused with -32070", async () => {
  const r = await post(base, { jsonrpc: "2.0", id: "u1", method: "task.create",
    params: { workspace: config.workspace, from: config.agent.uri, kind: "draft_message", input: {}, assignee: config.agent.uri } });
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
    oconfig.oidc = { issuer: issuer.issuer, jwks_url: issuer.jwks_url, audience: issuer.audience };
    ({ coord: ocoord, server: oserver, base: obase } = await startServer(oconfig));
    reviewer = makeClient({ url: `${obase}/chap`, workspace: oconfig.workspace, from: oconfig.humans[0].uri, signer: await generateSigner(oconfig.humans[0].uri) });
  });

  after(async () => {
    await new Promise((r) => oserver.close(r));
    await issuer.close();
  });

  test("the workspace advertises identity-oidc/1.0 and /api/config names the issuer", async () => {
    const cfg = await (await fetch(`${obase}/api/config`)).json();
    assert.equal(cfg.oidc.issuer, issuer.issuer);
    assert.ok(cfg.profiles.includes("identity-oidc/1.0"));
    assert.ok(ocoord.getWorkspace(oconfig.workspace).profiles.includes("identity-oidc/1.0"));
  });

  test("a join with a minted token pins cnf.jwk, and the human then signs with that key", async () => {
    const token = issuer.mintToken({ sub: "user-7f3c2a8e", uri: reviewer.from, cnfJwk: reviewer.signer.publicJwk, acr: "urn:example:authn:mfa" });
    // No jwks on the join: the only key the member gets is the one the token carries.
    const joined = await reviewer.call("participant.join", { type: "human", role: "reviewer", oidc_token: token });
    assert.equal(joined.joined, true);
    const created = await reviewer.call("task.create", { kind: "note", assignee: reviewer.from, input: { text: "signed with the pinned key" } });
    assert.equal(created.state, "created");
    const d = await reviewer.call("workspace.describe", {});
    const me = d.members.find((m) => m.uri === reviewer.from);
    assert.equal(me.oidc_sub, "user-7f3c2a8e");
    assert.deepEqual(me.jwks.keys.map((k) => k.kid), [reviewer.signer.kid]);
  });

  test("a token for another participant's URI is refused with -32404", async () => {
    const other = await generateSigner("human:someone-else@local");
    const token = issuer.mintToken({ sub: "user-other", uri: "human:someone-else@local", cnfJwk: other.publicJwk });
    const r = await post(obase, { jsonrpc: "2.0", id: "o1", method: "participant.join",
      params: { workspace: oconfig.workspace, from: "human:newcomer@local", type: "human", oidc_token: token } });
    assert.equal(r.error?.code, -32404);
    // A join under the reviewer's name with a token for a different subject is refused the same way.
    const stranger = issuer.mintToken({ sub: "user-other", uri: reviewer.from, cnfJwk: other.publicJwk });
    const r2 = await post(obase, { jsonrpc: "2.0", id: "o2", method: "participant.join",
      params: { workspace: oconfig.workspace, from: reviewer.from, type: "human", oidc_token: stranger } });
    assert.equal(r2.error?.code, -32404);
  });

  test("a garbage token, a token from an unknown key and an expired token are refused with -32403", async () => {
    const join = (oidc_token, id) => post(obase, { jsonrpc: "2.0", id, method: "participant.join",
      params: { workspace: oconfig.workspace, from: "human:probe@local", type: "human", oidc_token } });
    assert.equal((await join("garbage", "g1")).error?.code, -32403);
    assert.equal((await join("not.a.token", "g2")).error?.code, -32403);
    const rogue = generateKeyPairSync("ec", { namedCurve: "P-256" });
    const forged = signJwt({ privateKey: rogue.privateKey, kid: "rogue", payload: { iss: issuer.issuer, sub: "x", aud: issuer.audience, exp: Math.floor(Date.now() / 1000) + 600 } });
    assert.equal((await join(forged, "g3")).error?.code, -32403);
    const expired = issuer.mintToken({ sub: "x", uri: "human:probe@local", expires_in_sec: -3600 });
    assert.equal((await join(expired, "g4")).error?.code, -32403);
    const elsewhere = issuer.mintToken({ sub: "x", uri: "human:probe@local", audience: "another-service" });
    assert.equal((await join(elsewhere, "g5")).error?.code, -32403);
    assert.equal(ocoord.getWorkspace(oconfig.workspace).members.has("human:probe@local"), false);
  });

  test("the verifier checks signature, issuer, audience and time, and refetches the JWKS once on an unknown kid", async () => {
    let fetches = 0;
    const counting = (url, init) => { fetches++; return fetch(url, init); };
    const verify = await makeOidcVerifier({ issuer: issuer.issuer, jwks_url: issuer.jwks_url, audience: issuer.audience, fetchImpl: counting, refresh_min_ms: 0 });
    assert.equal(fetches, 1);
    const key = await generateSigner("human:claims@local");
    const good = verify(issuer.mintToken({ sub: "user-1", uri: "human:claims@local", cnfJwk: key.publicJwk, auth_time: 1747476000, acr: "mfa" }));
    assert.equal(good.sub, "user-1");
    assert.equal(good.iss, issuer.issuer);
    assert.equal(good.auth_time, 1747476000);
    assert.equal(good.acr, "mfa");
    assert.equal(good.chap_participant_uri, "human:claims@local");
    assert.deepEqual(good.cnf.jwk, key.publicJwk);

    const payload = { iss: issuer.issuer, sub: "user-1", aud: issuer.audience, exp: Math.floor(Date.now() / 1000) + 600 };
    const rogue = generateKeyPairSync("ec", { namedCurve: "P-256" });
    assert.equal(verify(signJwt({ privateKey: rogue.privateKey, kid: "unknown-kid", payload })), null);
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(fetches, 2, "an unknown kid fetched the JWKS again");
    assert.equal(verify(signJwt({ privateKey: rogue.privateKey, kid: issuer.kid, payload })), null, "a forged signature under the right kid");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", extra: { iss: "https://elsewhere.example" } })), null, "a wrong issuer");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", audience: "someone-else" })), null, "a wrong audience");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", expires_in_sec: -120 })), null, "an expired token");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", extra: { nbf: Math.floor(Date.now() / 1000) + 600 } })), null, "a token not yet valid");
    assert.equal(verify(issuer.mintToken({ sub: "user-1", extra: { sub: "" } })), null, "an empty subject");
    const noAudience = await makeOidcVerifier({ issuer: issuer.issuer, jwks_url: issuer.jwks_url, audience: null });
    assert.equal(noAudience(issuer.mintToken({ sub: "user-1", audience: "anything" })).sub, "user-1", "no audience configured means none is checked");
  });

  test("the doctor's checks pass against the in-process server with the issuer", async () => {
    const results = await runChecks({ url: `${obase}/chap`, dir: projectDir, keyPath, storePath: ":memory:" });
    const byName = Object.fromEntries(results.map((r) => [r.name, r]));
    for (const name of ["health", "config", "unsigned", "describe", "chain", "oidc"]) assert.equal(byName[name].status, "ok", `${name}: ${byName[name].detail}`);
    assert.equal(byName.store.status, "skip");
    assert.equal(results.filter((r) => r.status === "FAIL").length, 0);
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
