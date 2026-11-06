// The gate, end to end: an MCP client is the agent, the desk client is the
// human, signing as the desk does, and the coordinator refuses the agent's
// own approval and any decision in the human's name without the desk's key.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { fileURLToPath } from "node:url";
import { loadConfig, makeCoordinator, makeServer, AGENT_MCP_TOOLS } from "@brightbeamai/chap-server";
import { generateSigner, makeClient } from "@brightbeamai/chap-server/client";

let config, coord, server, base, mcp, desk;

before(async () => {
  config = await loadConfig(fileURLToPath(new URL("../chap.config.json", import.meta.url)));
  config.store = ":memory:";
  config.mcp = true;
  coord = await makeCoordinator(config);
  server = await makeServer(config, coord);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${server.address().port}`;
  mcp = new Client({ name: "gate-test", version: "0" });
  await mcp.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`)));
  // The human joins as the desk does, with a key made for them.
  desk = makeClient({ url: `${base}/chap`, workspace: config.workspace, from: config.humans[0].uri, signer: await generateSigner(config.humans[0].uri) });
  await desk.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [desk.signer.publicJwk] } });
});

after(async () => {
  await mcp?.close();
  await new Promise((r) => server.close(r));
});

const agent = () => config.agent.uri;
const human = () => config.humans[0].uri;

async function tool(name, args) {
  const r = await mcp.callTool({ name, arguments: { workspace: config.workspace, ...args } });
  const text = r.content.find((c) => c.type === "text")?.text ?? "{}";
  return { isError: !!r.isError, body: JSON.parse(text) };
}

async function rawTool(name, args) {
  const r = await mcp.callTool({ name, arguments: { workspace: config.workspace, ...args } });
  return { isError: !!r.isError, text: r.content.find((c) => c.type === "text")?.text ?? "" };
}

test("the MCP server offers the agent's tools, and none that would let it decide its own work", async () => {
  const { tools } = await mcp.listTools();
  assert.deepEqual(new Set(tools.map((t) => t.name)), AGENT_MCP_TOOLS);
  for (const name of ["chap.review.request", "chap.escalate.raise", "chap.participant.join",
                      "chap.workspace.set_profiles", "chap.control.cancel", "chap.handoff.propose"]) {
    assert.ok(!tools.some((t) => t.name === name), name);
    const r = await rawTool(name, { from: agent() });
    assert.equal(r.isError, true, name);
    assert.match(r.text, /not offered by this server/, name);
  }
});

test("the assistant cannot approve in the human's name", async () => {
  const created = await tool("chap.task.create", { from: agent(), kind: "draft_reply", assignee: agent(), input: { ticket: "T-2" }, review_required: true });
  const taskId = created.body.task_id;
  await tool("chap.task.complete", { from: agent(), task_id: taskId, output: { body: "Draft" } });

  const spoofed = await rawTool("chap.decide.approve", { from: human(), task_id: taskId });
  assert.equal(spoofed.isError, true);
  assert.match(spoofed.text, /acts as/);
  const view = await (await fetch(`${base}/api/tasks/${taskId}`)).json();
  assert.equal(view.state, "review_requested");

  // Leaving `from` out does not help either: the connection fills in the agent.
  const unnamed = await tool("chap.decide.approve", { task_id: taskId });
  assert.equal(unnamed.isError, true);
  assert.match(JSON.stringify(unnamed.body), /-32011|reviewer/);
});

test("a task the assistant completes waits for the human, and the assistant cannot approve it", async () => {
  const created = await tool("chap.task.create", { from: agent(), kind: "draft_reply", assignee: agent(), input: { ticket: "T-1" }, review_required: true });
  assert.equal(created.isError, false, JSON.stringify(created.body));
  const taskId = created.body.task_id;

  const done = await tool("chap.task.complete", { from: agent(), task_id: taskId, output: { body: "Draft reply" } });
  assert.equal(done.body.state, "review_requested");

  const reviews = await (await fetch(`${base}/api/reviews?reviewer=${encodeURIComponent(human())}`)).json();
  assert.ok(reviews.reviews.some((r) => r.task_id === taskId && r.artefact.body === "Draft reply"));

  const own = await tool("chap.decide.approve", { from: agent(), task_id: taskId });
  assert.equal(own.isError, true);
  assert.match(JSON.stringify(own.body), /-32011|reviewer/);

  const decided = await desk.call("decide.approve", { task_id: taskId, comment: "fine" });
  assert.equal(decided.state, "completed");

  const view = await (await fetch(`${base}/api/tasks/${taskId}`)).json();
  assert.equal(view.state, "completed");
  assert.deepEqual(view.output, { body: "Draft reply" });

  // The refused approval is on the chain as a refusal; the accepted calls are accepted.
  const { entries } = await desk.call("audit.read", { filter: { task_id: taskId } });
  assert.ok(entries.some((e) => e.outcome?.status === "refused" && e.request.method === "decide.approve"));
  assert.ok(entries.some((e) => e.envelope?.method === "decide.approve"));
});

test("a decision in the human's name counts only with the desk's signature, and the assistant's own calls need none", async () => {
  assert.equal(config.signature_policy, "humans");
  // Any local process can send this; the assistant's shell, say.
  const assistant = makeClient({ url: `${base}/chap`, workspace: config.workspace, from: agent() });
  const { task_id } = await assistant.call("task.create", { kind: "draft_reply", assignee: agent(), input: { ticket: "T-3" }, review_required: true });
  assert.equal((await assistant.call("task.complete", { task_id, output: { body: "Refund it" } })).state, "review_requested");
  const forged = makeClient({ url: `${base}/chap`, workspace: config.workspace, from: human() });
  await assert.rejects(forged.call("decide.approve", { task_id }), (e) => e.code === -32070, "an unsigned approval as the human is refused");
  assert.equal((await (await fetch(`${base}/api/tasks/${task_id}`)).json()).state, "review_requested");
  assert.equal((await desk.call("decide.approve", { task_id })).state, "completed", "the desk's signed approval counts");
});

test("the desk is served with its client module", async () => {
  const page = await (await fetch(`${base}/`)).text();
  assert.match(page, /CHAP review desk/);
  const mod = await (await fetch(`${base}/chap-client.mjs`)).text();
  assert.match(mod, /export function makeClient/);
  const cfg = await (await fetch(`${base}/api/config`)).json();
  assert.equal(cfg.workspace, config.workspace);
  assert.equal(cfg.mcp, true);
  assert.equal(cfg.chain_enabled, true, "the chain is on");
  assert.equal(cfg.desk_signing, "browser-key");
});

test("an MCP client on the 2026-07-28 revision is answered at /mcp, as the agent, with the agent's tools", async () => {
  const meta = { "io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {} };
  const post = (body) => fetch(`${base}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "mcp-protocol-version": "2026-07-28", "mcp-method": body.method, ...(body.params?.name ? { "mcp-name": body.params.name } : {}) },
    body: JSON.stringify(body),
  });
  const listed = await post({ jsonrpc: "2.0", id: 1, method: "tools/list", params: { _meta: meta } });
  assert.equal(listed.status, 200);
  assert.deepEqual(new Set((await listed.json()).result.tools.map((t) => t.name)), AGENT_MCP_TOOLS);
  const spoofed = await (await post({ jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "chap.decide.approve", arguments: { workspace: config.workspace, from: human(), task_id: "tsk_x" }, _meta: meta } })).json();
  assert.equal(spoofed.result.isError, true);
  assert.match(spoofed.result.content[0].text, /acts as/);
});

test("server.mjs and diff-profiles.mjs print their help with --help", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const dir = fileURLToPath(new URL("..", import.meta.url));
  for (const [script, pattern] of [["server.mjs", /npm start/], ["diff-profiles.mjs", /--against c,d/]]) {
    const { stdout } = await promisify(execFile)(process.execPath, [`${dir}${script}`, "--help"], { timeout: 10000 });
    assert.match(stdout, pattern, script);
  }
});

test("npm start on a port in use says so in one line", async () => {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const { createServer } = await import("node:http");
  const busy = createServer(() => {});
  await new Promise((r) => busy.listen(0, "127.0.0.1", r));
  const port = busy.address().port;
  try {
    const dir = fileURLToPath(new URL("..", import.meta.url));
    await assert.rejects(promisify(execFile)(process.execPath, [`${dir}server.mjs`], { env: { ...process.env, PORT: String(port), CHAP_DB_PATH: ":memory:" }, timeout: 10000 }),
      (e) => e.code === 1 && /^Port \d+ on 127\.0\.0\.1 is in use/.test(e.stderr) && e.stderr.trim().split("\n").length === 1);
  } finally {
    await new Promise((r) => busy.close(r));
  }
});
