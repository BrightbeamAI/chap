// The gate, end to end: an MCP client is the agent, the desk client is the
// human, and the coordinator refuses the agent's own approval.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { loadConfig, makeCoordinator, makeServer, AGENT_MCP_TOOLS } from "../server.mjs";
import { makeClient } from "../desk/chap-client.mjs";

let config, coord, server, base, mcp;

before(async () => {
  config = await loadConfig();
  config.store = ":memory:";
  config.mcp = true;
  coord = await makeCoordinator(config);
  server = await makeServer(config, coord);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${server.address().port}`;
  mcp = new Client({ name: "gate-test", version: "0" });
  await mcp.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`)));
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

  const desk = makeClient({ url: `${base}/chap`, workspace: config.workspace, from: human() });
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

test("the desk is served with its client module", async () => {
  const page = await (await fetch(`${base}/`)).text();
  assert.match(page, /CHAP review desk/);
  const mod = await (await fetch(`${base}/chap-client.mjs`)).text();
  assert.match(mod, /export function makeClient/);
  const cfg = await (await fetch(`${base}/api/config`)).json();
  assert.equal(cfg.workspace, config.workspace);
  assert.equal(cfg.mcp, true);
});
