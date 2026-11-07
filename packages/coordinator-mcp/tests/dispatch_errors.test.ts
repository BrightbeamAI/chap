/**
 * A dispatcher that throws is a fault in the server, and its message can
 * carry a path or a stack line. The tool error says only that the call
 * failed inside the server; the error itself goes to the server's log.
 *
 * Mirrors packages/coordinator-py/tests/test_mcp_dispatch_errors.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { makeChapMcpServer } from "../src/index.js";

test("a dispatcher's exception text stays in the server's log", async () => {
  const fault = new Error("ENOENT: no such file or directory, open '/srv/chap/data/chap.db'");
  const server = makeChapMcpServer({ dispatch() { throw fault; } });
  const client = new Client({ name: "test", version: "1.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(b), client.connect(a)]);

  const logged: unknown[][] = [];
  const consoleError = console.error;
  console.error = (...args: unknown[]) => { logged.push(args); };
  try {
    const r = await client.callTool({ name: "chap.workspace.describe", arguments: { workspace: "w" } }) as any;
    assert.equal(r.isError, true);
    assert.equal(r.content[0].text, "The CHAP call failed inside the server; its log has the detail.");
    assert.ok(!r.content[0].text.includes("/srv/chap"));
  } finally {
    console.error = consoleError;
    await client.close();
    await server.close();
  }
  assert.ok(logged.some((args) => args.includes(fault)), "the error is logged");
});

test("a coordinator's internal error reaches the client without its detail", async () => {
  // The coordinators answer a fault in a handler with -32603 and the
  // exception's text in data.detail; the detail goes to the log alone.
  const internal = { code: -32603, message: "Internal error", data: { detail: "rule.startsWith is not a function at /srv/chap/coordinator.js:1801" } };
  const server = makeChapMcpServer({ dispatch: (envelope) => ({ jsonrpc: "2.0", id: envelope.id, error: internal }) });
  const client = new Client({ name: "test", version: "1.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(b), client.connect(a)]);

  const logged: unknown[][] = [];
  const consoleError = console.error;
  console.error = (...args: unknown[]) => { logged.push(args); };
  try {
    const r = await client.callTool({ name: "chap.review.request", arguments: { workspace: "w" } }) as any;
    assert.equal(r.isError, true);
    assert.deepEqual(JSON.parse(r.content[0].text),
      { chap_error: -32603, message: "The CHAP call failed inside the server; its log has the detail." });
  } finally {
    console.error = consoleError;
    await client.close();
    await server.close();
  }
  assert.ok(logged.some((args) => args.includes(internal)), "the detail is logged");
});

test("a refusal is passed on as the coordinator gave it, data included", async () => {
  const refusal = { code: -32011, message: "Not an addressed reviewer for this task: agent:b", data: { reviewers: ["human:a"] } };
  const server = makeChapMcpServer({ dispatch: (envelope) => ({ jsonrpc: "2.0", id: envelope.id, error: refusal }) });
  const client = new Client({ name: "test", version: "1.0.0" });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await Promise.all([server.connect(b), client.connect(a)]);
  const r = await client.callTool({ name: "chap.decide.approve", arguments: { workspace: "w" } }) as any;
  assert.deepEqual(JSON.parse(r.content[0].text), { chap_error: -32011, message: refusal.message, data: refusal.data });
  await client.close();
  await server.close();
});
