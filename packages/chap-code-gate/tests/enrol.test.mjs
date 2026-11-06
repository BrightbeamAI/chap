// The trust policy at start: the reviewers and agents chap-trust.json pins
// join with their pinned keys before anyone can join under their names, so
// the first join of a reviewer's URI is no longer a way to take it.
import { test } from "node:test";
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { loadConfig } from "@brightbeamai/chap-server";
import { generateSigner, makeClient } from "@brightbeamai/chap-server/client";
import { startGate, trustedMembers } from "../index.mjs";
import { AGENT, HUMAN, gateProject } from "./helpers.mjs";

async function policyFor(dir, reviewer, agent) {
  await writeFile(join(dir, "chap-trust.json"), JSON.stringify({
    chap_trust: 1, workspace: "wsp_gate", rule: "any_one_approves",
    reviewers: { [HUMAN]: { name: "Rev Iewer", email: "rev@example.org", keys: [reviewer.publicJwk] } },
    agents: { [AGENT]: [agent.publicJwk] },
  }));
}

test("the gate joins the policy's reviewers with their pinned keys first, so a later join under the name holds nothing", async () => {
  const { dir, configPath } = await gateProject();
  const reviewer = await generateSigner(HUMAN);
  const agent = await generateSigner(AGENT);
  await policyFor(dir, reviewer, agent);
  const lines = [];
  const { server, base } = await startGate(await loadConfig(configPath), { log: (l) => lines.push(l) });
  try {
    assert.ok(lines.some((l) => /trust .*chap-trust\.json: 2 members joined with their pinned keys/.test(l)));
    const url = `${base}/chap`;
    const squat = await generateSigner(HUMAN);
    const squatter = makeClient({ url, workspace: "wsp_gate", from: HUMAN, signer: squat });
    await squatter.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [squat.publicJwk] } });
    const coder = makeClient({ url, workspace: "wsp_gate", from: AGENT, signer: agent });
    const { task_id } = await coder.call("task.create", { kind: "code_change", assignee: AGENT, input: { summary: "x" }, review_required: true });
    await coder.call("task.complete", { task_id, output: { summary: "x" } });
    await assert.rejects(squatter.call("decide.approve", { task_id }), (e) => [-32070, -32071].includes(e.code), "the squatter's key is not the reviewer's");
    const own = makeClient({ url, workspace: "wsp_gate", from: HUMAN, signer: reviewer });
    assert.equal((await own.call("decide.approve", { task_id })).state, "completed", "the pinned key decides");
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test("a trust policy the configuration names must be there, and must cover this workspace", async () => {
  const { configPath } = await gateProject({ trust: "nowhere/chap-trust.json" });
  await assert.rejects(trustedMembers(await loadConfig(configPath)), /names the trust policy .*nowhere\/chap-trust\.json, which cannot be read/);
  const other = await gateProject();
  await writeFile(join(other.dir, "chap-trust.json"), JSON.stringify({ chap_trust: 1, workspace: "wsp_other", reviewers: {}, agents: {} }));
  await assert.rejects(trustedMembers(await loadConfig(other.configPath)), /covers wsp_other, and chap\.config\.json names wsp_gate/);
  const none = await gateProject();
  assert.deepEqual(await trustedMembers(await loadConfig(none.configPath)), { path: null, members: [] }, "no policy, nothing to join");
});

test("a store that holds a reviewer under another key is reported at start", async () => {
  const { dir, configPath } = await gateProject();
  const config = await loadConfig(configPath);
  config.store = join(dir, "chap.db");
  const first = await startGate(config, { log: () => {} });
  const squat = await generateSigner(HUMAN);
  await makeClient({ url: `${first.base}/chap`, workspace: "wsp_gate", from: HUMAN, signer: squat }).call("participant.join", { type: "human", jwks: { keys: [squat.publicJwk] } });
  await new Promise((r) => first.server.close(r));
  await policyFor(dir, await generateSigner(HUMAN), await generateSigner(AGENT));
  const lines = [];
  const again = await startGate({ ...(await loadConfig(configPath)), store: config.store }, { log: (l) => lines.push(l) });
  try {
    assert.ok(lines.some((l) => l.includes(`${HUMAN} is on record in wsp_gate with a key the trust policy does not pin`)));
  } finally {
    await new Promise((r) => again.server.close(r));
  }
});
