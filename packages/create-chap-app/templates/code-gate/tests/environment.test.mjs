// What the hooks trust. They take the gate's address and the signature rule
// from chap.config.json; a gate's own /api/config does not tell them whether
// signatures are required, and CHAP_URL counts only where it names the gate
// the configuration describes, on this machine. Otherwise an agent could
// send its commits to a server of its own that approves anything.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig, makeCoordinator, makeServer } from "@brightbeamai/chap-server";
import { makeClient } from "@brightbeamai/chap-server/client";
import { contentHash, describeChange, loadGate, propose } from "@brightbeamai/chap-code-gate/gate";
import { git } from "@brightbeamai/chap-code-gate/git";
import { appendTo, demoRepo, projectDir, run } from "./helpers.mjs";

/** A gate with signatures off, serving the workspace the project names. */
async function unsignedGate() {
  const config = await loadConfig(join(projectDir, "chap.config.json"));
  Object.assign(config, { store: ":memory:", profiles: ["core/1.0", "review/1.0"], require_signatures: false, chain: false });
  const server = await makeServer(config, await makeCoordinator(config));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  return { config, server, base, url: `${base}/chap`, close: () => new Promise((r) => server.close(r)) };
}

/** A change approved there without a signature, in a repository under this project's hooks. */
async function approvedUnsigned(fake) {
  const repo = await demoRepo();
  await appendTo(repo, "README.md", "\nAnother line.\n");
  const gate = { dir: projectDir, config: fake.config, url: fake.url, base: fake.base };
  const agent = makeClient({ url: fake.url, workspace: fake.config.workspace, from: fake.config.agent.uri });
  await agent.call("participant.join", { type: "agent" });
  const human = makeClient({ url: fake.url, workspace: fake.config.workspace, from: fake.config.humans[0].uri });
  await human.call("participant.join", { type: "human", role: "reviewer" });
  const { task_id } = await propose(agent, await describeChange(repo, { summary: "another line" }), { gate });
  const view = await (await fetch(`${fake.base}/api/tasks/${task_id}`)).json();
  await human.call("decide.approve", { task_id, approved_artefact_digest: await contentHash(view.artefact), round: await contentHash(view.submission.envelope) });
  return repo;
}

/** chap.config.json as this project ships it, on `port`: signatures required. */
async function localConfig(port) {
  const config = JSON.parse(await (await import("node:fs/promises")).readFile(join(projectDir, "chap.config.json"), "utf8"));
  const path = join(await mkdtemp(join(tmpdir(), "chap-local-")), "chap.config.json");
  await writeFile(path, JSON.stringify({ ...config, port }));
  return path;
}

const commit = (repo, env) => run("git", ["commit", "-qam", "another line"], { cwd: repo, env: { ...process.env, CHAP_AUTOSTART: "0", CHAP_NO_BROWSER: "1", ...env } });

test("a CHAP_URL that names another server is not followed: the hooks ask the gate chap.config.json configures", async () => {
  const fake = await unsignedGate();
  try {
    const repo = await approvedUnsigned(fake);
    const config = await localConfig(1);
    await assert.rejects(commit(repo, { CHAP_CONFIG: config, CHAP_URL: fake.url }), (e) => /CHAP_URL names .* not the gate chap\.config\.json configures/.test(e.stderr) && /127\.0\.0\.1:1 is not answering/.test(e.stderr));
    assert.equal((await git(repo, ["rev-list", "--count", "HEAD"])).trim(), "1");
  } finally {
    await fake.close();
  }
});

test("the hooks take the signature rule from chap.config.json, and refuse an unsigned approval whatever the gate says", async () => {
  const fake = await unsignedGate();
  try {
    const repo = await approvedUnsigned(fake);
    const config = await localConfig(Number(new URL(fake.base).port));
    const served = await (await fetch(`${fake.base}/api/config`)).json();
    assert.equal(served.require_signatures, false, "the gate itself says signatures are off");
    await assert.rejects(commit(repo, { CHAP_CONFIG: config, CHAP_URL: fake.url }), (e) => /unsigned/.test(e.stderr));
    assert.equal((await git(repo, ["rev-list", "--count", "HEAD"])).trim(), "1");
  } finally {
    await fake.close();
  }
});

test("CHAP_URL is followed where it names the configured gate on this machine", async () => {
  const config = await localConfig(41234);
  const env = { CHAP_CONFIG: process.env.CHAP_CONFIG, CHAP_URL: process.env.CHAP_URL };
  try {
    process.env.CHAP_CONFIG = config;
    process.env.CHAP_URL = "http://localhost:41234/chap";
    assert.equal((await loadGate(projectDir)).url, "http://localhost:41234/chap");
    process.env.CHAP_URL = "http://127.0.0.1:41235/chap";
    assert.equal((await loadGate(projectDir, { say: () => {} })).url, "http://127.0.0.1:41234/chap");
    process.env.CHAP_URL = "https://gate.example.org/chap";
    assert.equal((await loadGate(projectDir, { say: () => {} })).url, "http://127.0.0.1:41234/chap");
  } finally {
    for (const [k, v] of Object.entries(env)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; }
  }
});
