// Signing people in with OIDC (review items 4 and A7). The server reads the
// issuer's discovery document and keys, requires the audience, and binds
// each token to a person chap.config.json names. The desk signs in with the
// authorisation code flow and PKCE, its nonce the thumbprint of the key it
// signs with, and joins with the ID token and that key, which the
// coordinator binds. The development issuer serves the flow for local runs
// and authenticates nobody.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createHash, generateKeyPairSync } from "node:crypto";
import { join } from "node:path";
import { IDBFactory } from "fake-indexeddb";
import { createChapServer, loadConfig } from "../index.mjs";
import { generateSigner, makeClient, thumbprint } from "../desk/chap-client.mjs";
import { DEV_AUDIENCE, signJwt, startDevIssuer } from "../lib/dev-issuer.mjs";
import { discover, forPeople, makeOidcVerifier } from "../lib/oidc.mjs";
import { openDesk } from "./desk-harness.mjs";
import { softwareAuthenticator } from "./webauthn-authenticator.mjs";
import { AGENT, HUMAN, project } from "./helpers.mjs";

let issuer;
const logged = [];
before(async () => { issuer = await startDevIssuer(0, { log: (l) => logged.push(l) }); });
after(() => issuer.close());

const b64url = (b) => Buffer.from(b).toString("base64url");
const pkce = () => { const verifier = b64url(Buffer.from("v".repeat(43))); return { verifier, challenge: b64url(createHash("sha256").update(verifier).digest()) }; };

/** The development issuer's authorisation endpoint, as a browser follows it: the redirect it answers with. */
async function authorize(params) {
  const url = new URL(`${issuer.issuer}/authorize`);
  for (const [k, v] of Object.entries({ response_type: "code", client_id: DEV_AUDIENCE, scope: "openid email", ...params })) if (v !== undefined) url.searchParams.set(k, v);
  const res = await fetch(url, { redirect: "manual" });
  return { status: res.status, location: res.headers.get("location") ? new URL(res.headers.get("location")) : null, text: await res.text() };
}

const tokenRequest = (form) => fetch(`${issuer.issuer}/token`, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(form) });

async function oidcGate(extra = {}) {
  const { dir } = await project({ require_signatures: true, oidc: { issuer: issuer.issuer, client_id: DEV_AUDIENCE }, ...extra });
  const lines = [];
  const chap = await createChapServer({ configPath: join(dir, "chap.config.json"), log: (l) => lines.push(l) });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: (l) => lines.push(l) });
  const signer = await generateSigner(AGENT);
  const agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT, signer });
  await agent.call("participant.join", { type: "agent", role: "drafter", jwks: { keys: [signer.publicJwk] } });
  return { chap, base, agent, lines };
}

test("the development issuer runs the authorisation code flow with PKCE for a redirect on this machine, and says it authenticates nobody", async () => {
  const doc = await discover(issuer.issuer);
  assert.equal(doc.chap_development_issuer, true);
  assert.deepEqual([doc.authorization_endpoint, doc.token_endpoint], [`${issuer.issuer}/authorize`, `${issuer.issuer}/token`]);
  const { verifier, challenge } = pkce();
  const redirect = "http://localhost:8790/";
  const sent = await authorize({ redirect_uri: redirect, state: "st", nonce: "nn", code_challenge: challenge, code_challenge_method: "S256", login_hint: HUMAN });
  assert.equal(sent.status, 302);
  assert.equal(`${sent.location.origin}${sent.location.pathname}`, redirect);
  assert.deepEqual([sent.location.searchParams.get("state"), sent.location.searchParams.get("iss")], ["st", issuer.issuer]);
  assert.ok(logged.includes(`The development issuer signed in ${HUMAN} with no question asked: it authenticates nobody.`));
  const code = sent.location.searchParams.get("code");
  const wrong = await tokenRequest({ grant_type: "authorization_code", code, redirect_uri: redirect, client_id: DEV_AUDIENCE, code_verifier: "another-verifier-of-enough-length-to-pass-x" });
  assert.equal((await wrong.json()).error, "invalid_grant", "a verifier that does not match the challenge");
  const sent2 = await authorize({ redirect_uri: redirect, state: "st", nonce: "nn", code_challenge: challenge, code_challenge_method: "S256", login_hint: HUMAN });
  const code2 = sent2.location.searchParams.get("code");
  const ok = await (await tokenRequest({ grant_type: "authorization_code", code: code2, redirect_uri: redirect, client_id: DEV_AUDIENCE, code_verifier: verifier })).json();
  const claims = JSON.parse(Buffer.from(ok.id_token.split(".")[1], "base64url"));
  assert.deepEqual([claims.iss, claims.sub, claims.aud, claims.azp, claims.nonce, claims.chap_participant_uri], [issuer.issuer, HUMAN, DEV_AUDIENCE, DEV_AUDIENCE, "nn", HUMAN]);
  assert.equal((await (await tokenRequest({ grant_type: "authorization_code", code: code2, redirect_uri: redirect, client_id: DEV_AUDIENCE, code_verifier: verifier })).json()).error, "invalid_grant", "a code is redeemed once");

  assert.equal((await authorize({ redirect_uri: "https://evil.example/", code_challenge: challenge, code_challenge_method: "S256", login_hint: HUMAN })).status, 400, "a code goes to this machine only");
  const noPkce = await authorize({ redirect_uri: redirect, state: "s", login_hint: HUMAN });
  assert.equal(noPkce.location.searchParams.get("error_description"), "PKCE with S256 is required");
  const nobody = await authorize({ redirect_uri: redirect, code_challenge: challenge, code_challenge_method: "S256" });
  assert.equal(nobody.location.searchParams.get("error"), "login_required");
});

test("the verifier reads the issuer's discovery document and keys, requires the audience, and passes the nonce on", async () => {
  await assert.rejects(makeOidcVerifier({ issuer: issuer.issuer }), /audience, the client id tokens are issued to, is required/);
  await assert.rejects(discover("http://idp.example.org"), /is not https; only an issuer on this machine, a development one, may be http/);
  await assert.rejects(discover(`${issuer.issuer}/`), /names the issuer .* and the configuration names .*\/; set the issuer exactly as the provider states it/);
  let fetches = 0;
  const verify = await makeOidcVerifier({ issuer: issuer.issuer, audience: DEV_AUDIENCE, fetchImpl: (u, i) => { fetches++; return fetch(u, i); } });
  assert.equal(fetches, 2, "the discovery document, then the keys it names");
  const good = verify(issuer.mintToken({ sub: "user-1", extra: { nonce: "abc" } }));
  assert.deepEqual([good.sub, good.nonce], ["user-1", "abc"]);
  const reason = (token) => verify.check(token).reason;
  assert.equal(reason(issuer.mintToken({ sub: "u", audience: "someone-else" })), `the token was issued to "someone-else", and the client here is ${DEV_AUDIENCE}`);
  assert.equal(reason(issuer.mintToken({ sub: "u", audience: [DEV_AUDIENCE, "other"] })), `the token was issued for null, and the client here is ${DEV_AUDIENCE}`, "several audiences and no azp");
  assert.equal(reason(issuer.mintToken({ sub: "u", extra: { azp: "other" } })), `the token was issued for "other", and the client here is ${DEV_AUDIENCE}`);
  assert.equal(reason(issuer.mintToken({ sub: "u", extra: { iss: "https://elsewhere.example" } })), `the token was issued by "https://elsewhere.example", and the issuer here is ${issuer.issuer}`);
  assert.equal(reason(issuer.mintToken({ sub: "u", expires_in_sec: -300 })), "the token has expired");
  const rogue = generateKeyPairSync("ec", { namedCurve: "P-256" });
  assert.equal(reason(signJwt({ privateKey: rogue.privateKey, kid: issuer.kid, payload: { iss: issuer.issuer, sub: "u", aud: DEV_AUDIENCE, exp: 2e9 } })), "the token's signature does not verify");
  assert.equal(reason("not.a.token"), "the token is not a signed JWT");
});

test("a token joins a person only as someone chap.config.json names: by chap_participant_uri, or by the subject their entry gives", async () => {
  const verify = await makeOidcVerifier({ issuer: issuer.issuer, audience: DEV_AUDIENCE });
  const people = forPeople(verify.check, [{ uri: HUMAN }, { uri: "human:ada@example.org", oidc_sub: "248289761001" }]);
  assert.equal(people(issuer.mintToken({ sub: "whoever", uri: HUMAN })).claims.chap_participant_uri, HUMAN);
  assert.equal(people(issuer.mintToken({ sub: "248289761001", extra: { email: "ada@example.org" } })).claims.chap_participant_uri, "human:ada@example.org", "the subject maps to the person");
  const stranger = people(issuer.mintToken({ sub: "999", extra: { email: "eve@example.org" } }));
  assert.deepEqual(stranger, { reason: 'signed in as eve@example.org, subject 999, whom chap.config.json does not name: add "oidc_sub": "999" to that person\'s entry in humans', subject: "999", email: "eve@example.org" });
  assert.match(people(issuer.mintToken({ sub: "x", uri: "human:mallory@example.org" })).reason, /names "human:mallory@example\.org", whom chap\.config\.json does not name among its people/);
  assert.match(people(issuer.mintToken({ sub: "777", uri: "human:ada@example.org" })).reason, /the token is for subject 777 and human:ada@example\.org signs in as subject 248289761001/);
});

test("the configuration names the issuer and the client id, and refuses an issuer with no client, or calls left unsigned", async () => {
  const signed = { require_signatures: true };
  const withClient = await loadConfig((await project({ ...signed, oidc: { issuer: "https://idp.example.org", client_id: "desk" } })).configPath);
  assert.deepEqual(withClient.oidc, { issuer: "https://idp.example.org", client_id: "desk" });
  const older = await loadConfig((await project({ ...signed, oidc: { issuer: "https://idp.example.org", audience: "chap-prod" } })).configPath);
  assert.equal(older.oidc.client_id, "chap-prod", "audience, the name before 0.3.5, is read as the client id");
  await assert.rejects(loadConfig((await project({ ...signed, oidc: { issuer: "https://idp.example.org" } })).configPath), /The OIDC issuer https:\/\/idp\.example\.org is set and no client id is: set OIDC_CLIENT_ID/);
  // Where people sign in from other machines, an unsigned agent is anyone who reaches the server.
  for (const require_signatures of ["humans", false]) {
    await assert.rejects(loadConfig((await project({ require_signatures, oidc: { issuer: "https://idp.example.org", client_id: "desk" } })).configPath),
      /signs people in at https:\/\/idp\.example\.org with "require_signatures" .*: set "require_signatures": true/, String(require_signatures));
  }
  // A store keeps the policy it was created with, so one made under
  // "humans" does not start where people sign in.
  const { dir, configPath } = await project({ require_signatures: "humans", store: "./data/chap.db" });
  await (await createChapServer({ configPath, log: () => {} })).close();
  const { writeFile, readFile } = await import("node:fs/promises");
  const later = JSON.parse(await readFile(configPath, "utf8"));
  await writeFile(configPath, JSON.stringify({ ...later, require_signatures: true, oidc: { issuer: issuer.issuer, client_id: DEV_AUDIENCE } }));
  await assert.rejects(createChapServer({ configPath, log: () => {} }),
    /was created before chap\.config\.json signed people in at .*, so it asks nobody for a token: start a new store/);
  // Nor does one made with every call signed, before anyone signed in.
  const allSigned = await project({ require_signatures: true, store: "./data/chap.db" });
  await (await createChapServer({ configPath: allSigned.configPath, log: () => {} })).close();
  const before = JSON.parse(await readFile(allSigned.configPath, "utf8"));
  await writeFile(allSigned.configPath, JSON.stringify({ ...before, oidc: { issuer: issuer.issuer, client_id: DEV_AUDIENCE } }));
  await assert.rejects(createChapServer({ configPath: allSigned.configPath, log: () => {} }), /so it asks nobody for a token: start a new store/);
  assert.ok(dir);
});

test("the server redeems a sign-in's code for the desk, holds the token to the nonce, and says who signed in when a token will not do", async () => {
  const { chap, base, lines } = await oidcGate();
  try {
    assert.ok(lines.some((l) => l.startsWith(`  oidc     people sign in at ${issuer.issuer}, as client ${DEV_AUDIENCE}; a development issuer, which authenticates nobody`)), lines.join("\n"));
    const cfg = await (await fetch(`${base}/api/config`)).json();
    assert.deepEqual(cfg.oidc, { issuer: issuer.issuer, client_id: DEV_AUDIENCE, authorization_endpoint: `${issuer.issuer}/authorize`, development: true });
    const redeem = async (hint, nonce, redeemNonce = nonce) => {
      const { verifier, challenge } = pkce();
      const sent = await authorize({ redirect_uri: `${base}/`, state: "s", nonce, code_challenge: challenge, code_challenge_method: "S256", login_hint: hint });
      const res = await fetch(`${base}/api/oidc/token`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ code: sent.location.searchParams.get("code"), code_verifier: verifier, redirect_uri: `${base}/`, nonce: redeemNonce }) });
      return { status: res.status, body: await res.json() };
    };
    const ok = await redeem(HUMAN, "n1");
    assert.equal(ok.status, 200);
    assert.deepEqual(Object.keys(ok.body).sort(), ["as", "id_token", "subject"], "the ID token and who it is for, and nothing else the provider sent");
    assert.equal(ok.body.as, HUMAN);
    const other = await redeem(HUMAN, "n2", "n3");
    assert.deepEqual([other.status, other.body.error], [400, "The ID token was issued for another sign-in; sign in again."]);
    const stranger = await redeem("human:mallory@example.org", "n4");
    assert.equal(stranger.status, 403);
    assert.match(stranger.body.error, /^The sign-in cannot join you: the token names "human:mallory@example\.org", whom chap\.config\.json does not name among its people\.$/);
    assert.equal((await fetch(`${base}/api/oidc/token`)).status, 405);
  } finally {
    await chap.close();
  }
});

test("a person's join needs a token, and a token whose nonce names another key binds nothing", async () => {
  const { chap, base } = await oidcGate();
  try {
    const key = await generateSigner(HUMAN);
    const me = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: HUMAN, signer: key });
    await assert.rejects(me.call("participant.join", { type: "human", jwks: { keys: [key.publicJwk] } }), (e) => e.code === -32403 && /An oidc_token is required/.test(e.message));
    const token = (nonce) => issuer.mintToken({ sub: HUMAN, uri: HUMAN, extra: { nonce } });
    await me.call("participant.join", { type: "human", jwks: { keys: [key.publicJwk] }, oidc_token: token(await thumbprint(key.publicJwk)) });
    const other = await generateSigner(HUMAN);
    await assert.rejects(makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: HUMAN }).call("participant.join", { type: "human", jwks: { keys: [other.publicJwk] }, oidc_token: token(await thumbprint(key.publicJwk)) }), (e) => e.code === -32404);
    assert.deepEqual(chap.coord.getWorkspace("wsp_test").members.get(HUMAN).keys.map((k) => k.kid), [key.kid], "the token bound the key its nonce names, and nothing else");
    const logged = chap.coord.getWorkspace("wsp_test").audit.filter((e) => (e.envelope ?? e.request)?.method === "participant.join" && (e.envelope ?? e.request).params.from === HUMAN);
    for (const e of logged) assert.match((e.envelope ?? e.request).params.oidc_token, /^sha256:[0-9a-f]{64}$/, "the chain keeps the token's digest");
  } finally {
    await chap.close();
  }
});

/** Sign in from the desk as a browser does: the click, the provider, and the page it sends the browser back to. */
async function signInFromDesk(base, desk, { indexedDB, authenticator }) {
  desk.$("setup-action").click();
  let pending;
  assert.ok(await desk.until(() => (pending = desk.w.sessionStorage.getItem("chap-desk-sign-in"))), "the sign-in is kept for the return");
  const kept = JSON.parse(pending);
  desk.close();
  // The provider, as the browser goes there and back.
  const challenge = b64url(createHash("sha256").update(kept.verifier).digest());
  const sent = await authorize({ redirect_uri: kept.redirect_uri, state: kept.state, nonce: kept.nonce, code_challenge: challenge, code_challenge_method: "S256", login_hint: kept.uri });
  return openDesk(base, { path: sent.location.search, indexedDB, authenticator, session: { "chap-desk-sign-in": pending } });
}

test("the desk signs in with the code flow and PKCE, its nonce its key's thumbprint, joins with the token, and its key is the one bound", async () => {
  const { chap, base, agent } = await oidcGate();
  const indexedDB = new IDBFactory();
  let desk = await openDesk(base, { indexedDB });
  try {
    assert.ok(await desk.until(() => !desk.$("setup-action").hidden && desk.$("setup-action").textContent === "Sign in"));
    assert.match(desk.text("setup-text"), new RegExp(`Sign in at ${issuer.issuer} to decide as Rev Iewer`));
    assert.deepEqual(desk.calls, [], "no join without a sign-in");
    desk = await signInFromDesk(base, desk, { indexedDB });
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")), "joined with the token");
    assert.equal(desk.w.location.search, "", "the code leaves the address at once");
    const joinCall = desk.calls.find((c) => c.method === "participant.join");
    const claims = JSON.parse(Buffer.from(joinCall.params.oidc_token.split(".")[1], "base64url"));
    const [key] = joinCall.params.jwks.keys;
    assert.equal(claims.nonce, await thumbprint(key), "the token's nonce is the thumbprint of the key the desk joins with");
    assert.deepEqual(chap.coord.getWorkspace("wsp_test").members.get(HUMAN).keys.map((k) => k.kid), [key.kid]);
    assert.equal(chap.coord.getWorkspace("wsp_test").members.get(HUMAN).oidc_sub, HUMAN);
    const { task_id } = await agent.call("task.create", { kind: "draft", assignee: AGENT, input: {}, review_required: true });
    await agent.call("task.complete", { task_id, output: { body: "x" } });
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.$("approve").click();
    assert.ok(await desk.until(() => chap.coord.getWorkspace("wsp_test").tasks.get(task_id).state === "completed"));
  } finally {
    desk.close();
    await chap.close();
  }
});

test("with a passkey, one click makes it and signs in, and the passkey is the key the sign-in binds", async () => {
  const { chap, base } = await oidcGate({ desk_signing: "webauthn" });
  const indexedDB = new IDBFactory();
  const authenticator = softwareAuthenticator({ platform: true });
  let desk = await openDesk(base, { indexedDB, authenticator });
  try {
    assert.ok(await desk.until(() => desk.$("setup-action").textContent === "Make a passkey and sign in"));
    desk = await signInFromDesk(base, desk, { indexedDB, authenticator });
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")), desk.text("setup-text"));
    const [jwk] = chap.coord.getWorkspace("wsp_test").members.get(HUMAN).keys.map((k) => k.jwk);
    assert.equal(jwk.kty, "EC");
    assert.ok(jwk.webauthn, "the passkey's key, bound by the sign-in");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("a sign-in for someone chap.config.json does not name is said in the desk, with the subject to add", async () => {
  const { chap, base } = await oidcGate({ humans: [{ uri: HUMAN, display_name: "Rev Iewer", role: "reviewer", oidc_sub: "248289761001" }] });
  const indexedDB = new IDBFactory();
  let desk = await openDesk(base, { indexedDB });
  try {
    assert.ok(await desk.until(() => !desk.$("setup-action").hidden));
    desk = await signInFromDesk(base, desk, { indexedDB });
    assert.ok(await desk.until(() => /The sign-in cannot join you/.test(desk.text("setup-text"))));
    assert.match(desk.text("setup-text"), new RegExp(`the token is for subject ${HUMAN.replace(/[.]/g, "\\.")} and ${HUMAN.replace(/[.]/g, "\\.")} signs in as subject 248289761001`));
    assert.ok(desk.$("setup").classList.contains("bad"));
    assert.equal(chap.coord.getWorkspace("wsp_test").members.has(HUMAN), false, "nobody joined");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("where people sign in, the read API answers a person signed in or a member's signed read, and nobody else", async () => {
  const { chap, base } = await oidcGate();
  try {
    for (const path of ["/api/tasks", "/api/members", "/api/chain", "/api/reviews", "/analytics/index.html"]) {
      const res = await fetch(`${base}${path}`);
      assert.equal(res.status, 401, path);
      assert.match((await res.json()).error, /sign in at the desk/);
    }
    for (const path of ["/api/config", "/api/health"]) assert.equal((await fetch(`${base}${path}`)).status, 200, path);

    // A member's key signs the read: the agent's, as the production agent
    // does, on a server where the agent joined with its key.
    const keyed = await import("../desk/chap-client.mjs");
    const agentSigner = await generateSigner(AGENT);
    const { dir } = await project({ require_signatures: true, oidc: { issuer: issuer.issuer, client_id: DEV_AUDIENCE } });
    const second = await createChapServer({ configPath: join(dir, "chap.config.json"), log: () => {} });
    const { base: other } = await second.listen({ port: 0, host: "127.0.0.1", log: () => {} });
    try {
      await makeClient({ url: `${other}/chap`, workspace: "wsp_test", from: AGENT, signer: agentSigner })
        .call("participant.join", { type: "agent", jwks: { keys: [agentSigner.publicJwk] } });
      const header = await keyed.signedRead(agentSigner, { workspace: "wsp_test", from: AGENT, path: "/api/tasks?limit=5" });
      assert.equal((await fetch(`${other}/api/tasks?limit=5`, { headers: { authorization: header } })).status, 200, "a signed read");
      assert.equal((await fetch(`${other}/api/members`, { headers: { authorization: header } })).status, 401, "signed for another path");
      const stranger = await generateSigner("agent:stranger");
      const forged = await keyed.signedRead(stranger, { workspace: "wsp_test", from: AGENT, path: "/api/tasks?limit=5" });
      assert.equal((await fetch(`${other}/api/tasks?limit=5`, { headers: { authorization: forged } })).status, 401, "a key the member does not hold");
      const stale = await agentSigner.sign({ jsonrpc: "2.0", id: "r", method: "workspace.describe",
        params: { workspace: "wsp_test", from: AGENT, path: "/api/tasks", ts: new Date(Date.now() - 10 * 60 * 1000).toISOString() } });
      assert.equal((await fetch(`${other}/api/tasks`, { headers: { authorization: `CHAP ${Buffer.from(JSON.stringify(stale)).toString("base64url")}` } })).status, 401, "a read signed ten minutes ago");
    } finally {
      await second.close();
    }

    // A person's ID token, sent as a bearer token.
    const { verifier, challenge } = pkce();
    const redirect = `${base}/`;
    const sent = await authorize({ redirect_uri: redirect, state: "s", nonce: "n", code_challenge: challenge, code_challenge_method: "S256", login_hint: HUMAN });
    const minted = await (await tokenRequest({ grant_type: "authorization_code", code: sent.location.searchParams.get("code"), redirect_uri: redirect, client_id: DEV_AUDIENCE, code_verifier: verifier })).json();
    assert.equal((await fetch(`${base}/api/tasks`, { headers: { authorization: `Bearer ${minted.id_token}` } })).status, 200, "a person's token");
    assert.equal((await fetch(`${base}/api/tasks`, { headers: { authorization: "Bearer not-a-token" } })).status, 401);
  } finally {
    await chap.close();
  }
});
