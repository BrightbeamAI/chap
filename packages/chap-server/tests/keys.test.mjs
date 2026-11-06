// The reviewer's signing key in the desk: made in the browser as a key no
// script can read out, kept in IndexedDB, named by its RFC 7638 thumbprint,
// shown by its whole fingerprint, and used only once the workspace holds it
// for the reviewer.
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { IDBFactory } from "fake-indexeddb";
import { createChapServer } from "../index.mjs";
import { contentHash, fingerprint, generateSigner, legacyKid, makeClient, thumbprint } from "../desk/chap-client.mjs";
import { openDesk } from "./desk-harness.mjs";
import { AGENT, HUMAN, project } from "./helpers.mjs";

const RFC8037_X = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo";

async function signedGate() {
  const { dir } = await project({ require_signatures: true });
  const chap = await createChapServer({ configPath: join(dir, "chap.config.json") });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  const signer = await generateSigner(AGENT);
  const agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT, signer });
  await agent.call("participant.join", { type: "agent", jwks: { keys: [signer.publicJwk] } });
  return { chap, base, agent };
}

/** A task waiting for the reviewer: created and completed by the agent. */
async function waiting(agent, summary) {
  const { task_id } = await agent.call("task.create", { kind: "draft", assignee: AGENT, input: { summary }, review_required: true });
  await agent.call("task.complete", { task_id, output: { body: summary } });
  return task_id;
}

/** The record the desk keeps in IndexedDB for a reviewer. */
function stored(indexedDB, key) {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("chap-desk", 1);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const get = open.result.transaction("keys").objectStore("keys").get(key);
      get.onsuccess = () => { open.result.close(); resolve(get.result); };
      get.onerror = () => reject(get.error);
    };
  });
}

test("a key's id is its RFC 7638 thumbprint (RFC 8037, appendix A.3)", async () => {
  assert.equal(await thumbprint({ kty: "OKP", crv: "Ed25519", x: RFC8037_X }), "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k");
});

test("the desk makes a key no script can read out, keeps it in IndexedDB under its thumbprint, shows its whole fingerprint, and signs with it", async () => {
  const { chap, base, agent } = await signedGate();
  const indexedDB = new IDBFactory();
  let desk = null;
  try {
    desk = await openDesk(base, { indexedDB });
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")), "the workspace holds the desk's key");
    assert.deepEqual(Object.keys(desk.w.localStorage).filter((k) => k.startsWith("chap-desk-key")), [], "nothing in localStorage");
    const record = await stored(indexedDB, `wsp_test ${HUMAN}`);
    assert.equal(record.privateKey.extractable, false);
    await assert.rejects(crypto.subtle.exportKey("jwk", record.privateKey));
    assert.equal(record.publicJwk.kid, await thumbprint(record.publicJwk));
    const onRecord = chap.coord.getWorkspace("wsp_test").members.get(HUMAN).keys.map((k) => k.jwk);
    assert.deepEqual(onRecord.map((k) => [k.kid, k.x]), [[record.publicJwk.kid, record.publicJwk.x]]);
    const print = await fingerprint(record.publicJwk);
    assert.match(print, /^SHA256:[A-Za-z0-9+/]{43}$/);
    assert.equal(desk.text("key-fingerprint"), print, "the whole fingerprint is shown");
    assert.equal(desk.text("key-id"), record.publicJwk.kid);
    desk.close();

    const task = await waiting(agent, "approve me");
    desk = await openDesk(base, { indexedDB, hash: `#task=${task}` });
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.$("approve").click();
    assert.ok(await desk.until(() => chap.coord.getWorkspace("wsp_test").tasks.get(task).state === "completed"), "the approval signed with the stored key is accepted");
  } finally {
    desk?.close();
    await chap.close();
  }
});

test("when the workspace refuses the desk's join because another key holds the name, the desk says so and signs nothing", async () => {
  const { chap, base, agent } = await signedGate();
  const squat = await generateSigner(HUMAN);
  await makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: HUMAN, signer: squat }).call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [squat.publicJwk] } });
  const task = await waiting(agent, "approve me");
  const desk = await openDesk(base, { hash: `#task=${task}` });
  try {
    assert.ok(await desk.until(() => !desk.$("key-alert").hidden), "the desk shows that another key holds the name");
    const join = desk.calls.find((c) => c.method === "participant.join");
    assert.equal(join.reply.error.code, -32011, "the coordinator refuses the join");
    assert.deepEqual(join.reply.error.data, { kids: [join.params.jwks.keys[0].kid] });
    assert.match(desk.text("key-alert"), new RegExp(`The workspace refused this browser's key when the desk joined you, because another key already holds ${HUMAN} in wsp_test\\.`));
    assert.match(desk.text("signing"), /not signing/);
    assert.equal(desk.$("approve").disabled, true);
    desk.$("approve").click();
    desk.key("x");
    await desk.settle(300);
    assert.deepEqual(desk.calls.map((c) => c.method), ["participant.join"], "nothing is signed: no read, no decision");
    assert.equal(chap.coord.getWorkspace("wsp_test").tasks.get(task).state, "review_requested");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("a member who joined under the reviewer's name with no key takes none, and the desk says so and signs nothing", async () => {
  const { chap, base, agent } = await signedGate();
  const bare = await fetch(`${base}/chap`, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: "bare", method: "participant.join", params: { workspace: "wsp_test", from: HUMAN, type: "human", role: "reviewer" } }) });
  assert.deepEqual((await bare.json()).result, { joined: true, as: HUMAN });
  const task = await waiting(agent, "approve me");
  const desk = await openDesk(base, { hash: `#task=${task}` });
  try {
    assert.ok(await desk.until(() => !desk.$("key-alert").hidden), "the desk shows that the name takes no key");
    assert.equal(desk.calls.find((c) => c.method === "participant.join").reply.error.code, -32011, "the coordinator refuses the join");
    const said = new RegExp(`^The workspace takes no key for you\\. The workspace refused this browser's key when the desk joined you: ${HUMAN} is a member of wsp_test with no key, and a member with no key takes none later\\.`);
    assert.match(desk.text("key-alert"), said);
    assert.match(desk.text("signing"), /no key/);
    await desk.settle(4500);
    assert.match(desk.text("key-alert"), said, "the next poll, which reads the members again, says the same");
    assert.equal(desk.$("approve").disabled, true);
    assert.deepEqual(desk.calls.map((c) => c.method), ["participant.join"], "nothing is signed");
    assert.equal(chap.coord.getWorkspace("wsp_test").tasks.get(task).state, "review_requested");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("a key the workspace no longer holds: the decision is refused with -32072, and the desk reads the members again and stops", async () => {
  const { chap, base, agent } = await signedGate();
  const desk = await openDesk(base);
  try {
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")), "the desk's key is on record");
    const task = await waiting(agent, "approve me");
    assert.ok(await desk.until(() => !desk.$("approve").disabled), "the review waits in the desk");
    const kid = chap.coord.getWorkspace("wsp_test").members.get(HUMAN).keys[0].kid;
    const adminSigner = await generateSigner("human:admin@example.org");
    const admin = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: "human:admin@example.org", signer: adminSigner });
    await admin.call("participant.join", { type: "human", role: "admin", jwks: { keys: [adminSigner.publicJwk] } });
    await admin.call("participant.revoke_key", { target_uri: HUMAN, kid, reason: "suspected_compromise" });
    desk.$("approve").click();
    assert.ok(await desk.until(() => !desk.$("key-alert").hidden), "the desk says the workspace holds no key for the reviewer");
    const refused = desk.calls.find((c) => c.method === "decide.approve");
    assert.equal(refused.reply.error.code, -32072);
    assert.match(desk.text("key-alert"), /The workspace holds no key for you now\. The key this browser signed with .* was revoked or rotated away/);
    assert.equal(desk.$("approve").disabled, true);
    assert.equal(chap.coord.getWorkspace("wsp_test").tasks.get(task).state, "review_requested");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("a key an earlier desk kept in localStorage moves into IndexedDB under the kid it was registered with, and still signs", async () => {
  const { chap, base, agent } = await signedGate();
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const jwk = await crypto.subtle.exportKey("jwk", pair.privateKey);
  const kid = await legacyKid(HUMAN);
  await makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: HUMAN }).call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [{ kty: "OKP", crv: "Ed25519", kid, use: "sig", alg: "EdDSA", x: jwk.x }] } });
  const task = await waiting(agent, "approve me");
  const indexedDB = new IDBFactory();
  const name = `chap-desk-key:wsp_test:${HUMAN}`;
  const desk = await openDesk(base, { indexedDB, hash: `#task=${task}`, storage: { [name]: JSON.stringify(jwk) } });
  try {
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")), "the moved key is the one on record");
    assert.equal(desk.w.localStorage.getItem(name), null, "the copy in localStorage is gone");
    const record = await stored(indexedDB, `wsp_test ${HUMAN}`);
    assert.equal(record.publicJwk.kid, kid);
    assert.equal(record.privateKey.extractable, false);
    assert.match(desk.w.document.querySelector(".toast")?.textContent ?? "", /local storage/);
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.$("approve").click();
    assert.ok(await desk.until(() => chap.coord.getWorkspace("wsp_test").tasks.get(task).state === "completed"));
    const approval = desk.calls.find((c) => c.method === "decide.approve");
    assert.equal(approval.reply.result.state, "completed");
    assert.ok(approval.params.approved_artefact_digest === await contentHash({ body: "approve me" }));
  } finally {
    desk.close();
    await chap.close();
  }
});
