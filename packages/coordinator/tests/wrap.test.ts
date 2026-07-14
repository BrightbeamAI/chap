/**
 * Tests for the inward wrap helpers (``src/transports/wrap.ts``).
 *
 * Mirrors packages/coordinator-py/tests/test_wrap_helpers.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  Coordinator,
  contentHash,
  wrapMcpToolCall,
  wrapA2aMessageExchange,
} from "../src/index.js";

function makeCoord(): Coordinator {
  const coord = new Coordinator({
    deterministicIds: true,
    deterministicClock: true,
    defaultProfiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"],
  });
  coord.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "wsp_wrap" }});
  coord.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "wsp_wrap", from: "agent:bot",
              type: "agent", role: "drafter" }});
  coord.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.join",
    params: { workspace: "wsp_wrap", from: "service:a2a-bridge",
              type: "service", role: "bridge" }});
  return coord;
}

// ============================================================
// contentHash
// ============================================================

test("contentHash format", () => {
  const h = contentHash({ a: 1, b: [1, 2, 3] });
  assert.ok(h.startsWith("sha256:"));
  assert.equal(h.length, 7 + 64);
});

test("contentHash is canonical (JCS sorts keys)", () => {
  const h1 = contentHash({ a: 1, b: 2 });
  const h2 = contentHash({ b: 2, a: 1 });
  assert.equal(h1, h2);
});

test("contentHash differs on content", () => {
  assert.notEqual(contentHash({ a: 1 }), contentHash({ a: 2 }));
});

// ============================================================
// wrapMcpToolCall
// ============================================================

test("wrapMcpToolCall emits task.create + task.complete", () => {
  const coord = makeCoord();
  const res = wrapMcpToolCall(coord, "wsp_wrap", {
    caller: "agent:bot",
    tool: "github.create_issue",
    server: "github",
    args: { title: "bug", body: "details" },
    result: { issue_url: "https://github.com/example/repo/issues/1" },
    confidence: "0.95",
  });
  assert.ok(res.task_id.startsWith("tsk_"));
  assert.ok(res.input_hash.startsWith("sha256:"));
  assert.ok(res.output_hash.startsWith("sha256:"));

  const ws = coord.getWorkspace("wsp_wrap")!;
  const task = ws.tasks.get(res.task_id)!;
  assert.equal(task.state, "completed");
  assert.equal(task.kind, "mcp_call:github.create_issue");
  assert.equal(task.assignee, "agent:bot");
  assert.equal(task.delegator, "agent:bot");

  const output = task.output as { result: { issue_url: string };
    citations: Array<{ server: string; tool: string; input_hash: string; output_hash: string }>};
  assert.equal(output.result.issue_url, "https://github.com/example/repo/issues/1");
  assert.equal(output.citations.length, 1);
  assert.equal(output.citations[0].server, "github");
  assert.equal(output.citations[0].tool, "github.create_issue");
  assert.equal(output.citations[0].input_hash, res.input_hash);
  assert.equal(output.citations[0].output_hash, res.output_hash);
});

test("wrapMcpToolCall routing hints attach to task", () => {
  const coord = makeCoord();
  const res = wrapMcpToolCall(coord, "wsp_wrap", {
    caller: "agent:bot", tool: "some.tool",
    args: {}, result: { ok: true },
    routingHints: { criticality: "low", risk_tier: "standard" },
  });
  const ws = coord.getWorkspace("wsp_wrap")!;
  const task = ws.tasks.get(res.task_id)!;
  assert.equal(task.routing_hints?.criticality, "low");
  assert.equal(task.routing_hints?.risk_tier, "standard");
});

test("wrapMcpToolCall validates required args", () => {
  const coord = makeCoord();
  assert.throws(() => wrapMcpToolCall(coord, "",
    { caller: "x", tool: "y", args: {}, result: {} }), /workspace/);
  assert.throws(() => wrapMcpToolCall(coord, "wsp_wrap",
    { caller: "", tool: "y", args: {}, result: {} }), /caller/);
  assert.throws(() => wrapMcpToolCall(coord, "wsp_wrap",
    { caller: "agent:bot", tool: "", args: {}, result: {} }), /tool/);
});

test("wrapMcpToolCall CHAP error throws", () => {
  const coord = makeCoord();
  assert.throws(() => wrapMcpToolCall(coord, "wsp_wrap", {
    caller: "agent:not-joined",
    tool: "t", args: {}, result: {},
  }), /task\.create failed/);
});

test("wrapMcpToolCall lands in audit log", () => {
  const coord = makeCoord();
  const before = coord.getWorkspace("wsp_wrap")!.audit.length;
  wrapMcpToolCall(coord, "wsp_wrap", {
    caller: "agent:bot", tool: "t", args: {}, result: { ok: true },
  });
  const after = coord.getWorkspace("wsp_wrap")!.audit.length;
  // task.create + task.update + task.complete = 3 envelopes
  assert.equal(after - before, 3);
});

// ============================================================
// wrapA2aMessageExchange
// ============================================================

test("wrapA2aMessageExchange basic", () => {
  const coord = makeCoord();
  const res = wrapA2aMessageExchange(coord, "wsp_wrap", {
    bridgeUri: "service:a2a-bridge",
    remoteAgent: "a2a:partner-org/agent-1",
    sent: { task: "summarise", doc: "hello world" },
    received: { summary: "Hello, world." },
    confidence: "0.9",
  });
  assert.ok(res.task_id.startsWith("tsk_"));
  const ws = coord.getWorkspace("wsp_wrap")!;
  const task = ws.tasks.get(res.task_id)!;
  assert.equal(task.state, "completed");
  assert.equal(task.kind, "a2a_exchange");
  assert.equal(task.assignee, "service:a2a-bridge");
  const input = task.input as { remote_agent: string };
  assert.equal(input.remote_agent, "a2a:partner-org/agent-1");
  const output = task.output as {
    received: { summary: string };
    citations: Array<{ kind: string; remote_agent: string; sent_hash: string; received_hash: string }>;
  };
  assert.equal(output.received.summary, "Hello, world.");

  const citation = output.citations[0];
  assert.equal(citation.kind, "a2a_exchange");
  assert.equal(citation.remote_agent, "a2a:partner-org/agent-1");
  assert.equal(citation.sent_hash, res.sent_hash);
  assert.equal(citation.received_hash, res.received_hash);
});

test("wrapA2aMessageExchange validates required args", () => {
  const coord = makeCoord();
  assert.throws(() => wrapA2aMessageExchange(coord, "",
    { bridgeUri: "b", remoteAgent: "r", sent: {}, received: {} }),
    /workspace/);
  assert.throws(() => wrapA2aMessageExchange(coord, "wsp_wrap",
    { bridgeUri: "", remoteAgent: "r", sent: {}, received: {} }),
    /bridgeUri/);
  assert.throws(() => wrapA2aMessageExchange(coord, "wsp_wrap",
    { bridgeUri: "service:a2a-bridge", remoteAgent: "", sent: {}, received: {} }),
    /remoteAgent/);
});
