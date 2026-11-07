// The signature policy as a project runs it. Under require_signatures
// "humans" a person's calls are signed and an agent member's need not be:
// /mcp, which acts as the agent unsigned, works from its first call, and an
// unsigned decision in a person's name is refused. A store keeps the policy
// its workspace was made with, and the console says what the server runs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { request } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createChapServer, loadConfig, makeCoordinator } from "../index.mjs";
import { generateSigner, makeClient, signerFromJwk } from "../desk/chap-client.mjs";
import { generateKeyFile, readKeyFile } from "../lib/keys.mjs";
import { AGENT, HUMAN, project } from "./helpers.mjs";

const META = { "io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {} };

/** A tool call to /mcp, as an MCP client on the 2026-07-28 revision sends it. */
async function mcpTool(base, name, args) {
  const res = await fetch(`${base}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call", "mcp-name": name },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: { workspace: "wsp_test", ...args }, _meta: META } }),
  });
  const reply = await res.json();
  return { isError: !!reply.result?.isError, text: reply.result?.content?.[0]?.text ?? JSON.stringify(reply) };
}

async function humansServer(extra = {}) {
  const { dir, configPath } = await project({ require_signatures: "humans", mcp: true, ...extra });
  const lines = [];
  const chap = await createChapServer({ configPath, log: (l) => lines.push(l) });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: (l) => lines.push(l) });
  return { dir, chap, base, lines };
}

test("under \"humans\" the agent works unsigned from its first call, and a decision in a person's name counts only signed", async () => {
  const { chap, base, lines } = await humansServer();
  try {
    assert.ok(lines.includes("  signing  people sign every call; an agent member's calls need no signature"), lines.join("\n"));
    const created = await mcpTool(base, "chap.task.create", { kind: "draft", assignee: AGENT, input: { summary: "hello" }, review_required: true });
    assert.equal(created.isError, false, created.text);
    const taskId = JSON.parse(created.text).task_id;

    const signer = await generateSigner(HUMAN);
    const desk = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: HUMAN, signer });
    await desk.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [signer.publicJwk] } });
    const agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT });
    assert.equal((await agent.call("task.complete", { task_id: taskId, output: { body: "hello" } })).state, "review_requested", "the agent's unsigned call is taken");

    const unsigned = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: HUMAN });
    await assert.rejects(unsigned.call("decide.approve", { task_id: taskId }), (e) => e.code === -32070, "an unsigned decision as the person is refused");
    assert.equal((await desk.call("decide.approve", { task_id: taskId })).state, "completed", "the desk's signed decision counts");
  } finally {
    await chap.close();
  }
});

test("the agent /mcp joins keeps the key its key file holds, so the same agent signs as well", async () => {
  const { dir, configPath } = await project({ require_signatures: "humans", mcp: true });
  await generateKeyFile(AGENT, join(dir, "keys"));
  const chap = await createChapServer({ configPath, log: () => {} });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  try {
    const read = await mcpTool(base, "chap.workspace.describe", {});
    assert.equal(read.isError, false, read.text);
    const key = await readKeyFile(AGENT, join(dir, "keys", "agent_drafter.jwk.json"));
    const signed = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT, signer: await signerFromJwk(AGENT, key) });
    assert.deepEqual(await signed.call("participant.join", { type: "agent", jwks: { keys: [signed.signer.publicJwk] } }), { joined: true, as: AGENT });
    assert.equal((await signed.call("task.create", { kind: "draft", assignee: AGENT, input: { n: 1 } })).state, "created");
    assert.deepEqual(chap.coord.getWorkspace("wsp_test").members.get(AGENT).keys.map((k) => k.kid), [key.kid]);
  } finally {
    await chap.close();
  }
});

test("a store keeps the signature policy its workspace was made with, and the console and /api/config say which", async () => {
  const store = join(await mkdtemp(join(tmpdir(), "chap-policy-")), "chap.db");
  const unsigned = await project({ require_signatures: false, store });
  const first = await createChapServer({ configPath: unsigned.configPath, log: () => {} });
  await first.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  await first.close();
  const later = await project({ require_signatures: "humans", store });
  const lines = [];
  const config = await loadConfig(later.configPath);
  const again = await createChapServer({ config, log: (l) => lines.push(l) });
  const { base } = await again.listen({ port: 0, host: "127.0.0.1", log: (l) => lines.push(l) });
  try {
    assert.ok(lines.some((l) => l === "wsp_test was created under other settings, and runs with every call after the join is signed. require_signatures in chap.config.json applies to a new store."), lines.join("\n"));
    const cfg = await (await fetch(`${base}/api/config`)).json();
    assert.deepEqual([cfg.require_signatures, cfg.signature_policy], [true, "all"]);
    assert.ok(lines.includes("  signing  every call after the join is signed"));
  } finally {
    await again.close();
  }
});

test("the server admits only the members chap.config.json names, so a process cannot add a person of its own and decide as them", async () => {
  const { chap, base } = await humansServer();
  try {
    const signer = await generateSigner("human:sock@local");
    const sock = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: "human:sock@local", signer });
    await assert.rejects(sock.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [signer.publicJwk] } }),
      (e) => e.code === -32011 && /not a member chap\.config\.json names/.test(e.message));
    assert.equal(chap.coord.getWorkspace("wsp_test").members.has("human:sock@local"), false);
  } finally {
    await chap.close();
  }
});

test("/mcp answers under a name the server is configured to serve, as the rest of the server does", async () => {
  const { chap, base } = await humansServer({ allowed_hosts: ["gate.internal:8080"] });
  try {
    // fetch cannot set Host, so the requests go through node:http.
    const post = (host) => new Promise((resolve, reject) => {
      const body = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "t", version: "1" } } });
      const req = request(`${base}/mcp`, { method: "POST", headers: { host, "content-type": "application/json", accept: "application/json, text/event-stream", "mcp-protocol-version": "2025-11-25" } },
        (res) => { res.resume(); res.on("end", () => resolve(res.statusCode)); });
      req.on("error", reject);
      req.end(body);
    });
    assert.equal(await post("gate.internal:8080"), 200, "a configured name reaches /mcp");
    assert.equal(await post("evil.example:8080"), 421, "any other name is refused");
  } finally {
    await chap.close();
  }
});

test("the read API shows what another process wrote to the store", async () => {
  const { dir, configPath } = await project({ require_signatures: "humans", store: "chap.db" });
  const chap = await createChapServer({ configPath, log: () => {} });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  const other = await makeCoordinator(await loadConfig(configPath), { log: () => {} });
  try {
    const joined = other.dispatch({ jsonrpc: "2.0", id: 0, method: "participant.join", params: { workspace: "wsp_test", from: AGENT, type: "agent" } });
    assert.ok(joined.result, JSON.stringify(joined));
    const r = other.dispatch({ jsonrpc: "2.0", id: 1, method: "task.create", params: { workspace: "wsp_test", from: AGENT, kind: "draft", assignee: AGENT, input: { summary: "from elsewhere" } } });
    assert.ok(r.result, JSON.stringify(r));
    await other.drainSaves();
    const { tasks } = await (await fetch(`${base}/api/tasks`)).json();
    assert.ok(tasks.some((t) => t.task_id === r.result.task_id), "the desk's read API reads the other process's task");
  } finally {
    await chap.close();
    other.close?.();
  }
  void dir;
});
