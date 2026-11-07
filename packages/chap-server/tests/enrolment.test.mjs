// The enrolment code (pre-publish review S3). Where signatures are
// required and nobody signs in at an identity provider, the first join of a
// person chap.config.json names holds that name with the key it carries.
// That join needs the code the server printed when it started, so a process
// that cannot see the server's console, an agent among them, cannot take a
// person's name before they open the desk. The desk takes the code from the
// link the server printed, or asks for it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { createChapServer, enrolmentCode, loadConfig } from "../index.mjs";
import { generateSigner, makeClient } from "../desk/chap-client.mjs";
import { openDesk } from "./desk-harness.mjs";
import { AGENT, HUMAN, project } from "./helpers.mjs";

const CODE = process.env.CHAP_ENROLMENT_CODE;

async function signedGate(extra = {}) {
  const { dir } = await project({ require_signatures: "humans", ...extra });
  const lines = [];
  const chap = await createChapServer({ configPath: join(dir, "chap.config.json"), log: () => {} });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: (l) => lines.push(l) });
  return { chap, base, dir, lines };
}

const joinAs = async (base, uri, signer, enrolment) => {
  const client = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: uri, signer, enrolment });
  return client.call("participant.join", { type: uri.split(":")[0], jwks: { keys: [signer.publicJwk] } });
};

test("a person's first join needs the code; nothing is recorded without it; the agent's join and a later one need none", async () => {
  const { chap, base, lines } = await signedGate();
  try {
    const squat = await generateSigner(HUMAN);
    for (const code of [undefined, "WRONG-CODE0"]) {
      await assert.rejects(joinAs(base, HUMAN, squat, code), (e) => e.code === -32011 && e.data?.reason === "enrolment"
        && /joins for the first time with the enrolment code the desk server printed/.test(e.message));
    }
    const ws = chap.coord.getWorkspace("wsp_test");
    assert.equal(ws.members.has(HUMAN), false);
    assert.equal(ws.audit.filter((e) => (e.envelope ?? e.request)?.params?.from === HUMAN).length, 0, "a refused first join is not recorded");
    await joinAs(base, AGENT, await generateSigner(AGENT));
    const person = await generateSigner(HUMAN);
    await joinAs(base, HUMAN, person, CODE.toLowerCase().replace("-", " "));
    assert.equal(ws.members.has(HUMAN), true, "the code is read in any case, with or without its hyphen");
    await joinAs(base, HUMAN, person);
    const config = await (await fetch(`${base}/api/config`)).json();
    assert.equal(config.enrolment, true);
    assert.ok(!JSON.stringify(config).includes(CODE), "the code is never served");
    assert.ok(lines.some((l) => l.includes(`enrol    ${CODE}`) && l.includes(`${base}/#enrol=${CODE}`)), lines.join("\n"));
  } finally {
    await chap.close();
  }
});

test("no code where signatures are off, where people sign in, or where chap.config.json turns it off", async () => {
  for (const extra of [{ require_signatures: false }, { require_signatures: "humans", enrolment: false }]) {
    const { configPath } = await project(extra);
    assert.equal(enrolmentCode(await loadConfig(configPath)), null, JSON.stringify(extra));
  }
  const { configPath } = await project({ require_signatures: "humans" });
  const config = await loadConfig(configPath);
  config.oidc = { issuer: "http://127.0.0.1:1", client_id: "desk" };
  assert.equal(enrolmentCode(config), null, "where people sign in, the token binds them");
});

test("the server keeps the code beside a store on disk, readable by this user alone", async () => {
  const { chap, dir } = await signedGate({ store: "./data/chap.db" });
  try {
    const kept = join(dir, "data", "enrolment-code");
    assert.equal((await readFile(kept, "utf8")).trim(), CODE);
    const { stat } = await import("node:fs/promises");
    assert.equal((await stat(kept)).mode & 0o077, 0, "no one else reads it");
  } finally {
    await chap.close();
  }
});

test("the desk takes the code from the link the server printed, or asks for it once", async () => {
  const { chap, base } = await signedGate();
  try {
    const asked = [];
    let desk = await openDesk(base, { enrol: null, prompt: (message) => { asked.push(message); return null; } });
    try {
      assert.ok(await desk.until(() => asked.length === 1), "the desk asks for the code");
      assert.match(asked[0], /joins this workspace for the first time\. Enter the enrolment code/);
      assert.equal(chap.coord.getWorkspace("wsp_test").members.has(HUMAN), false);
    } finally {
      desk.close();
    }
    desk = await openDesk(base, { enrol: null, prompt: () => CODE });
    try {
      assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")), "joined with the code typed in");
    } finally {
      desk.close();
    }
    const { chap: second, base: other } = await signedGate();
    desk = await openDesk(other, { enrol: CODE, prompt: () => { throw new Error("asked"); } });
    try {
      assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")), "joined with the code from the link");
      assert.equal(desk.w.location.hash.includes("enrol"), false, "the code is taken off the address");
    } finally {
      desk.close();
      await second.close();
    }
  } finally {
    await chap.close();
  }
});
