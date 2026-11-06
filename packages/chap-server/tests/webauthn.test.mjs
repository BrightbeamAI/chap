// The desk signing with a passkey. desk_signing "auto" makes one where the
// browser has an authenticator built in that verifies its user, and keeps a
// key in the browser otherwise, with a notice of what that key cannot
// protect against. A passkey signs each call with navigator.credentials.get,
// user verification required, over the SHA-256 of the envelope's canonical
// form, and the coordinator checks the assertion as a relying party does.
// The browser here is jsdom with a software authenticator holding a real
// P-256 key per credential (webauthn-authenticator.mjs).
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, createPublicKey } from "node:crypto";
import { join } from "node:path";
import { IDBFactory } from "fake-indexeddb";
import { createChapServer } from "../index.mjs";
import { canonicalize, generateSigner, makeClient, thumbprint } from "../desk/chap-client.mjs";
import { atLocalhost, signingFor } from "../desk/signing.js";
import { openDesk } from "./desk-harness.mjs";
import { assertionSignature, authenticatorData, softwareAuthenticator } from "./webauthn-authenticator.mjs";
import { AGENT, HUMAN, project } from "./helpers.mjs";

async function gate(extra = {}) {
  const { dir } = await project({ require_signatures: "humans", desk_signing: "auto", ...extra });
  const chap = await createChapServer({ configPath: join(dir, "chap.config.json"), log: () => {} });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  // Under "humans" the agent's calls go unsigned, from a member.
  const agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT });
  await agent.call("participant.join", { type: "agent", role: "drafter" });
  return { chap, base, agent, port: chap.server.address().port };
}

/** A task the agent created and completed, waiting for the reviewer. */
async function waiting(agent, summary) {
  const { task_id } = await agent.call("task.create", { kind: "draft", assignee: AGENT, input: { summary }, review_required: true });
  await agent.call("task.complete", { task_id, output: { body: summary } });
  return task_id;
}

const post = async (base, envelope) => (await fetch(`${base}/chap`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(envelope) })).json();
const b64url = (b) => Buffer.from(b).toString("base64url");

test("where the browser has a platform authenticator, the desk makes a passkey on the reviewer's click, registers it in the shape the coordinator reads, and its approval is accepted", async () => {
  const { chap, base, agent, port } = await gate();
  const authenticator = softwareAuthenticator({ platform: true });
  const desk = await openDesk(base, { authenticator });
  try {
    assert.equal(base, `http://localhost:${port}`);
    assert.ok(await desk.until(() => !desk.$("setup").hidden && !desk.$("setup-action").hidden), "the desk asks for a passkey");
    assert.match(desk.text("setup-text"), /signed with a passkey, which asks for your fingerprint, face or PIN each time/);
    assert.deepEqual(desk.calls, [], "nothing is joined or signed before the passkey");
    desk.$("setup-action").click();
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")), "the passkey is on record");
    const made = authenticator.asked.find((a) => a.op === "create").publicKey;
    assert.equal(made.rp.id, "localhost");
    assert.equal(made.authenticatorSelection.userVerification, "required");
    assert.equal(made.authenticatorSelection.authenticatorAttachment, "platform");
    assert.deepEqual(made.pubKeyCredParams, [{ type: "public-key", alg: -7 }]);

    const [jwk] = chap.coord.getWorkspace("wsp_test").members.get(HUMAN).keys.map((k) => k.jwk);
    assert.deepEqual(Object.keys(jwk).sort(), ["alg", "crv", "kid", "kty", "use", "webauthn", "x", "y"]);
    assert.deepEqual([jwk.kty, jwk.crv, jwk.alg, jwk.use], ["EC", "P-256", "ES256", "sig"]);
    assert.equal(jwk.kid, await thumbprint(jwk), "the kid is the key's RFC 7638 thumbprint");
    assert.deepEqual(jwk.webauthn, { rp_id: "localhost", origins: [`http://localhost:${port}`] });
    const [credential] = authenticator.credentials.values();
    assert.deepEqual(createPublicKey({ key: { kty: "EC", crv: "P-256", x: jwk.x, y: jwk.y }, format: "jwk" }).export({ format: "der", type: "spki" }), credential.publicKey.export({ format: "der", type: "spki" }), "the key on record is the authenticator's");
    assert.match(desk.text("key-kind"), /A passkey/);

    const task = await waiting(agent, "approve me");
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.$("approve").click();
    assert.ok(await desk.until(() => chap.coord.getWorkspace("wsp_test").tasks.get(task).state === "completed"), "the approval the passkey signed is accepted");
    const approval = desk.calls.find((c) => c.method === "decide.approve").envelope;
    const [scheme, kid, assertion] = approval.sig.split(":");
    assert.deepEqual([scheme, kid], ["webauthn", jwk.kid]);
    assert.equal(assertion.split(".").length, 3);
    const asked = authenticator.asked.filter((a) => a.op === "get").at(-1).publicKey;
    const { sig: _sig, ...unsigned } = approval;
    assert.deepEqual(Buffer.from(asked.challenge), createHash("sha256").update(canonicalize(unsigned)).digest(), "the challenge is the SHA-256 of the envelope's canonical form without sig");
    assert.equal(asked.userVerification, "required");
    assert.equal(asked.rpId, "localhost");
    assert.deepEqual(desk.blocked, []);
  } finally {
    desk.close();
    await chap.close();
  }
});

test("a passkey signature that was tampered with, sent again, made without user verification or from another origin is refused", async () => {
  const { chap, base, agent } = await gate();
  const authenticator = softwareAuthenticator({ platform: true });
  const desk = await openDesk(base, { authenticator });
  try {
    assert.ok(await desk.until(() => !desk.$("setup-action").hidden));
    desk.$("setup-action").click();
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")));
    const task = await waiting(agent, "keep me");
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.$("comment").value = "a note";
    desk.$("changes").click();
    assert.ok(await desk.until(() => desk.calls.some((c) => c.method === "decide.reject")));
    const sent = desk.calls.find((c) => c.method === "decide.reject").envelope;
    assert.equal(desk.calls.find((c) => c.method === "decide.reject").reply.result.state, "in_progress");

    const tampered = await post(base, { ...sent, params: { ...sent.params, comment: "approve it all" } });
    assert.equal(tampered.error.code, -32070);
    assert.equal(tampered.error.message, "WebAuthn challenge is not the digest of this request");
    const replayed = await post(base, sent);
    assert.equal(replayed.error.code, -32075, "a signed request takes effect once");

    // Assertions the reviewer never gave: the authenticator's own key, with
    // the user-verified flag left off, or for a page at another origin.
    const [credential] = authenticator.credentials.values();
    const [, kid] = sent.sig.split(":");
    const forge = (flags, origin) => {
      const envelope = { jsonrpc: "2.0", id: `forged-${flags}-${origin}`, method: "decide.reject", params: { ...sent.params, comment: "forged" } };
      const challenge = createHash("sha256").update(canonicalize(envelope)).digest();
      const authData = authenticatorData("localhost", flags, 99);
      const clientData = Buffer.from(JSON.stringify({ type: "webauthn.get", challenge: b64url(challenge), origin, crossOrigin: false }));
      return { ...envelope, sig: `webauthn:${kid}:${b64url(authData)}.${b64url(clientData)}.${b64url(assertionSignature(credential.privateKey, authData, clientData))}` };
    };
    const origin = new URL(base).origin;
    const unverified = await post(base, forge(0x01, origin));
    assert.deepEqual([unverified.error.code, unverified.error.message], [-32070, "WebAuthn assertion lacks user presence or verification"]);
    const elsewhere = await post(base, forge(0x05, "http://localhost:1"));
    assert.deepEqual([elsewhere.error.code, elsewhere.error.message], [-32070, "WebAuthn origin is not one the key allows"]);
    assert.equal(chap.coord.getWorkspace("wsp_test").tasks.get(task).state, "in_progress", "none of them decided anything");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("without a platform authenticator, the desk signs with a key it keeps in the browser and says what that key cannot protect against", async () => {
  const { chap, base, agent } = await gate();
  const desk = await openDesk(base, { authenticator: softwareAuthenticator({ platform: false }) });
  try {
    assert.ok(await desk.until(() => !desk.$("setup").hidden && desk.$("signing").classList.contains("warn")), "the browser key, marked");
    assert.match(desk.text("setup-text"), /This browser has no passkey with a fingerprint, face or PIN check, so the desk signs with a key it keeps in the browser\. A program running as you on this machine, a coding agent with a shell among them, can have the browser sign with that key/);
    assert.match(desk.text("signing"), /^browser key · /);
    const task = await waiting(agent, "approve me");
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.$("approve").click();
    assert.ok(await desk.until(() => chap.coord.getWorkspace("wsp_test").tasks.get(task).state === "completed"));
    assert.match(desk.calls.find((c) => c.method === "decide.approve").envelope.sig, /^ed25519:/);
    desk.$("setup-close").click();
    assert.equal(desk.$("setup").hidden, true, "the notice is put away once read");
    assert.ok(desk.$("signing").classList.contains("warn"), "the chip still says it");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("a passkey check the reviewer cancels sends nothing", async () => {
  const { chap, base, agent } = await gate();
  const authenticator = softwareAuthenticator({ platform: true });
  const desk = await openDesk(base, { authenticator });
  try {
    assert.ok(await desk.until(() => !desk.$("setup-action").hidden));
    desk.$("setup-action").click();
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")));
    const task = await waiting(agent, "approve me");
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    authenticator.verifies = false;
    desk.$("approve").click();
    assert.ok(await desk.until(() => /Nothing was sent: the passkey check was cancelled, or timed out\./.test(desk.text("notice"))));
    assert.deepEqual(desk.calls.filter((c) => c.method.startsWith("decide.")), []);
    assert.equal(chap.coord.getWorkspace("wsp_test").tasks.get(task).state, "review_requested");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("under desk_signing \"webauthn\", a name the browser key holds moves to a passkey with a rotation that key signs", async () => {
  const { chap, base, agent } = await gate({ desk_signing: "browser-key" });
  const indexedDB = new IDBFactory();
  const authenticator = softwareAuthenticator({ platform: true });
  let desk = await openDesk(base, { indexedDB, authenticator });
  try {
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")));
    const [browserKey] = chap.coord.getWorkspace("wsp_test").members.get(HUMAN).keys;
    assert.equal(browserKey.jwk.kty, "OKP");
    desk.close();
    chap.config.desk_signing = "webauthn";
    desk = await openDesk(base, { indexedDB, authenticator });
    assert.ok(await desk.until(() => !desk.$("setup-action").hidden), "the desk offers the move");
    assert.equal(desk.$("approve").disabled, true, "and signs nothing with the browser key meanwhile");
    desk.$("setup-action").click();
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good") && desk.$("setup").hidden));
    const rotation = desk.calls.find((c) => c.method === "participant.rotate_key");
    assert.equal(rotation.reply.result.rotated, true);
    assert.match(rotation.envelope.sig, new RegExp(`^ed25519:${browserKey.kid}:`), "the browser key signs the move");
    const keys = chap.coord.getWorkspace("wsp_test").members.get(HUMAN).keys;
    assert.ok(keys.find((k) => k.kid === browserKey.kid).valid_until, "the browser key is given up");
    const current = keys.filter((k) => !k.valid_until).map((k) => k.jwk);
    assert.deepEqual(current.map((k) => k.kty), ["EC"]);
    const task = await waiting(agent, "approve me");
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.$("approve").click();
    assert.ok(await desk.until(() => chap.coord.getWorkspace("wsp_test").tasks.get(task).state === "completed"));
    assert.match(desk.calls.find((c) => c.method === "decide.approve").envelope.sig, new RegExp(`^webauthn:${current[0].kid}:`));
  } finally {
    desk.close();
    await chap.close();
  }
});

test("opened at the loopback address, a desk that wants a passkey goes to localhost, since a passkey cannot be made for an IP address", async () => {
  const { chap, port } = await gate();
  const desk = await openDesk(`http://127.0.0.1:${port}`, { authenticator: softwareAuthenticator({ platform: true }), hash: "#task=tsk_1" });
  try {
    assert.ok(await desk.until(() => !desk.$("setup").hidden));
    assert.equal(desk.text("setup-text"), `A passkey is made for a name, never an IP address, so the desk opens at http://localhost:${port}/#task=tsk_1&reviewer=${encodeURIComponent(HUMAN)}.`);
    assert.deepEqual(desk.calls, [], "nothing is joined at the IP address");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("what the desk signs with, by desk_signing, the browser and the address", async () => {
  const PKC = (platform) => class { static isUserVerifyingPlatformAuthenticatorAvailable() { return Promise.resolve(platform); } };
  const at = (href) => new URL(href);
  const creds = {};
  assert.equal(await signingFor("browser-key", at("http://localhost:1/"), PKC(true), creds), "browser-key");
  assert.equal(await signingFor("auto", at("http://localhost:1/"), PKC(true), creds), "passkey");
  assert.equal(await signingFor("auto", at("http://localhost:1/"), PKC(false), creds), "browser-key");
  assert.equal(await signingFor("auto", at("http://localhost:1/"), undefined, undefined), "browser-key", "a browser with no WebAuthn");
  assert.equal(await signingFor("auto", at("http://127.0.0.1:1/"), PKC(true), creds), "localhost");
  assert.equal(await signingFor("auto", at("http://[::1]:1/"), PKC(true), creds), "localhost");
  assert.equal(await signingFor("auto", at("http://192.168.1.4:1/"), PKC(true), creds), "browser-key");
  assert.equal(await signingFor("webauthn", at("http://localhost:1/"), PKC(false), creds), "passkey", "a security key or a phone will do");
  assert.equal(await signingFor("webauthn", at("http://192.168.1.4:1/"), PKC(true), creds), "no passkey");
  assert.equal(await signingFor("webauthn", at("https://desk.example.org/"), undefined, undefined), "no passkey");
  assert.equal(atLocalhost(at("http://127.0.0.1:8791/review/?a=1#task=t")), "http://localhost:8791/review/?a=1#task=t");
});
