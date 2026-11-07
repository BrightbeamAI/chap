/**
 * The tool filter decides what the server offers: a tool it excludes is
 * refused when called by name, as well as left out of `tools/list`. And
 * `participant` makes every call as one member, so a connection meant for
 * one agent cannot act in any other member's name, nor in a person's.
 *
 * Mirrors packages/coordinator-py/tests/test_mcp_filter_and_participant.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { AGENT_MCP_TOOLS, chapMcpHttpHandler, makeChapMcpServer, type ChapMcpOptions } from "../src/index.js";

async function connect(options: ChapMcpOptions) {
  const coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0", "review/1.0"] });
  coord.dispatch({ jsonrpc: "2.0", id: 1, method: "workspace.create",
                   params: { workspace: "w", from: "human:a", profiles: ["core/1.0", "review/1.0"] } } as never);
  for (const [uri, type] of [["human:a", "human"], ["agent:b", "agent"]]) {
    coord.dispatch({ jsonrpc: "2.0", id: uri, method: "participant.join",
                     params: { workspace: "w", from: uri, type } } as never);
  }
  const server = makeChapMcpServer(coord, options);
  const client = new Client({ name: "test", version: "1.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(b), client.connect(a)]);
  const call = async (name: string, args: Record<string, unknown>) => {
    const r = await client.callTool({ name, arguments: { workspace: "w", ...args } }) as any;
    return { isError: !!r.isError, text: r.content[0]?.text as string };
  };
  return { coord, client, call, close: async () => { await client.close(); await server.close(); } };
}

test("a tool the filter excludes is refused, as well as left out of the list", async () => {
  const { client, call, close } = await connect({ toolFilter: (name) => name !== "chap.task.create" });
  const { tools } = await client.listTools();
  assert.ok(!tools.some((t) => t.name === "chap.task.create"));
  const r = await call("chap.task.create", { from: "agent:b", kind: "k", input: {}, assignee: "agent:b" });
  assert.equal(r.isError, true);
  assert.match(r.text, /not offered by this server: chap\.task\.create/);
  await close();
});

test("participant fills in from, and refuses a call that names another", async () => {
  const { coord, call, close } = await connect({ participant: "agent:b" });
  const made = await call("chap.task.create", { kind: "k", input: {}, assignee: "agent:b" });
  assert.equal(made.isError, false, made.text);
  const taskId = JSON.parse(made.text).task_id;
  assert.equal((coord as any).workspaces.get("w").tasks.get(taskId).delegator, "agent:b");

  const same = await call("chap.task.update", { from: "agent:b", task_id: taskId, state: "in_progress" });
  assert.equal(same.isError, false, same.text);

  const other = await call("chap.task.update", { from: "human:a", task_id: taskId, state: "declined" });
  assert.equal(other.isError, true);
  assert.match(other.text, /acts as agent:b/);
  assert.equal((coord as any).workspaces.get("w").tasks.get(taskId).state, "in_progress");
  await close();
});

test("without the new options every tool runs and each call names its own from", async () => {
  const { coord, call, close } = await connect({});
  const made = await call("chap.task.create", { from: "human:a", kind: "k", input: {}, assignee: "agent:b" });
  assert.equal(made.isError, false, made.text);
  const taskId = JSON.parse(made.text).task_id;
  assert.equal((coord as any).workspaces.get("w").tasks.get(taskId).delegator, "human:a");
  await close();
});

test("with participant, the tools list from as optional and say whose name it carries", async () => {
  const { client, close } = await connect({ participant: "agent:b" });
  const { tools } = await client.listTools();
  const update = tools.find((t) => t.name === "chap.task.update")!;
  const schema = update.inputSchema as { required: string[]; properties: Record<string, { description: string }> };
  assert.ok(!schema.required.includes("from"));
  assert.ok(schema.required.includes("task_id"));
  assert.match(schema.properties.from.description, /acts as agent:b/);
  await close();
});

test("an agent asked to review acknowledges and decides through its own connection", async () => {
  const { coord, call, close } = await connect({ participant: "agent:b", toolFilter: (name) => AGENT_MCP_TOOLS.has(name) });
  coord.dispatch({ jsonrpc: "2.0", id: "c", method: "participant.join", params: { workspace: "w", from: "agent:c", type: "agent" } } as never);
  const made = coord.dispatch({ jsonrpc: "2.0", id: "t", method: "task.create",
                                params: { workspace: "w", from: "agent:c", kind: "k", input: {}, assignee: "agent:c" } } as never);
  const taskId = (made.result as { task_id: string }).task_id;
  coord.dispatch({ jsonrpc: "2.0", id: "r", method: "review.request",
                   params: { workspace: "w", from: "agent:c", task_id: taskId, to: ["agent:b"], artefact: { v: 1 } } } as never);

  const acknowledged = await call("chap.review.acknowledge", { task_id: taskId });
  assert.equal(acknowledged.isError, false, acknowledged.text);
  assert.equal(JSON.parse(acknowledged.text).acknowledged, true);
  const approved = await call("chap.decide.approve", { task_id: taskId });
  assert.equal(approved.isError, false, approved.text);
  await close();
});

test("a person cannot be the participant: a server is a connection for an agent", () => {
  const coord = new Coordinator({ deterministicIds: true });
  for (const make of [makeChapMcpServer, chapMcpHttpHandler]) {
    assert.throws(() => make(coord, { participant: "human:a" }), /participant human:a names a person/);
  }
  makeChapMcpServer(coord, { participant: "agent:b" });
});

test("a dispatcher that answers with a promise is awaited", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const server = makeChapMcpServer({ dispatch: async (envelope) => coord.dispatch(envelope) });
  const client = new Client({ name: "test", version: "1.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(b), client.connect(a)]);
  const r = await client.callTool({ name: "chap.workspace.create", arguments: { workspace: "w", from: "human:a" } }) as any;
  assert.equal(r.isError, undefined, r.content[0].text);
  assert.equal(JSON.parse(r.content[0].text).workspace, "w");
  await client.close();
  await server.close();
});
