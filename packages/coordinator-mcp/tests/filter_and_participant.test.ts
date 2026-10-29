/**
 * The tool filter decides what the server offers, and `participant` makes
 * every call as one member.
 *
 * Before, `toolFilter` only left tools out of `tools/list`: a client that
 * called an excluded tool by name still ran it. And every call named its own
 * `from`, so a connection meant for one agent could act in any member's name.
 *
 * Mirrors packages/coordinator-py/tests/test_mcp_filter_and_participant.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { makeChapMcpServer, type ChapMcpOptions } from "../src/index.js";

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
