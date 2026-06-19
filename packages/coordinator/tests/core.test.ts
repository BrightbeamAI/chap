import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

function makeCoord(): Coordinator {
  return new Coordinator({
    deterministicIds: true,
    deterministicClock: true,
  });
}

function send(coord: Coordinator, method: string, params: Record<string, unknown>) {
  return coord.dispatch({ jsonrpc: "2.0", id: `t-${method}`, method, params });
}

test("workspace.create returns the workspace id", () => {
  const c = makeCoord();
  const r = send(c, "workspace.create", { workspace: "wsp_a" });
  assert.ok("result" in r && !r.error);
  assert.equal((r.result as { workspace: string }).workspace, "wsp_a");
});

test("workspace.describe on unknown workspace errors", () => {
  const c = makeCoord();
  const r = send(c, "workspace.describe", { workspace: "wsp_missing" });
  assert.ok(r.error);
});

test("participant.join auto-creates the workspace", () => {
  const c = makeCoord();
  const r = send(c, "participant.join", {
    workspace: "wsp_auto", from: "human:alice", type: "human", role: "reviewer",
  });
  assert.ok("result" in r && (r.result as { joined: boolean }).joined === true);
});

test("task.create requires the assignee be a member", () => {
  const c = makeCoord();
  send(c, "workspace.create", { workspace: "wsp_t" });
  const r = send(c, "task.create", {
    workspace: "wsp_t", from: "human:alice", kind: "k", input: {},
    assignee: "agent:not-joined",
  });
  assert.ok(r.error);
});

test("task lifecycle: create -> in_progress -> completed", () => {
  const c = makeCoord();
  send(c, "workspace.create", { workspace: "wsp_t" });
  send(c, "participant.join", { workspace: "wsp_t", from: "human:alice", type: "human", role: "owner" });
  send(c, "participant.join", { workspace: "wsp_t", from: "agent:bot", type: "agent", role: "drafter" });

  const r1 = send(c, "task.create", {
    workspace: "wsp_t", from: "human:alice", kind: "draft",
    input: { q: "?" }, assignee: "agent:bot",
  });
  const tid = (r1.result as { task_id: string }).task_id;

  const r2 = send(c, "task.update", {
    workspace: "wsp_t", task_id: tid, state: "in_progress", from: "agent:bot",
  });
  assert.equal((r2.result as { state: string }).state, "in_progress");

  const r3 = send(c, "task.complete", {
    workspace: "wsp_t", task_id: tid, output: { ok: true }, from: "agent:bot",
  });
  assert.equal((r3.result as { state: string }).state, "completed");
});

test("illegal task transition is rejected", () => {
  const c = makeCoord();
  send(c, "workspace.create", { workspace: "wsp_t" });
  send(c, "participant.join", { workspace: "wsp_t", from: "human:alice", type: "human", role: "owner" });
  send(c, "participant.join", { workspace: "wsp_t", from: "agent:bot", type: "agent", role: "drafter" });
  const r = send(c, "task.create", { workspace: "wsp_t", from: "human:alice",
    kind: "k", input: {}, assignee: "agent:bot" });
  const tid = (r.result as { task_id: string }).task_id;
  // created -> completed (skipping in_progress) is illegal
  const r2 = send(c, "task.update", { workspace: "wsp_t", task_id: tid, state: "completed", from: "agent:bot" });
  assert.ok(r2.error);
});

test("unknown method returns -32601", () => {
  const c = makeCoord();
  const r = send(c, "does.not.exist", { workspace: "x" });
  assert.equal(r.error?.code, -32601);
});

test("malformed envelope returns -32600", () => {
  const c = makeCoord();
  const r = c.dispatch({ not_jsonrpc: true } as never);
  assert.equal(r.error?.code, -32600);
});
