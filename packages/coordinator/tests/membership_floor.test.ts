/**
 * Regression: task.create / task.update require membership; set_profiles requires
 * the admin role; audit.read / workspace.describe are transport-delegated by
 * default but gated by requireReadMembership. Mirrors test_membership_floor.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

function memberWs(opts = {}) {
  const c = new Coordinator(opts);
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "w", profiles: ["core/1.0"] }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "agent:bot", type: "agent" }});
  return c;
}

test("task.create requires membership", () => {
  const c = memberWs();
  const r = c.dispatch({ jsonrpc: "2.0", id: "t", method: "task.create",
    params: { workspace: "w", from: "human:stranger", kind: "k", input: {}, assignee: "agent:bot" }});
  assert.equal(r.error?.code, -32011);
});

test("task.update requires membership", () => {
  const c = memberWs();
  const tc = c.dispatch({ jsonrpc: "2.0", id: "c", method: "task.create",
    params: { workspace: "w", from: "agent:bot", kind: "k", input: {}, assignee: "agent:bot" }});
  const tid = (tc.result as { task_id: string }).task_id;
  const r = c.dispatch({ jsonrpc: "2.0", id: "u", method: "task.update",
    params: { workspace: "w", from: "human:stranger", task_id: tid, state: "in_progress" }});
  assert.equal(r.error?.code, -32011);
});

test("set_profiles requires the admin role", () => {
  const c = new Coordinator({});
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create", params: { workspace: "w" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human" }});
  c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.join",
    params: { workspace: "w", from: "human:admin", type: "human", role: "admin" }});
  const setProfiles = (from: string) => c.dispatch({ jsonrpc: "2.0", id: "s",
    method: "workspace.set_profiles", params: { workspace: "w", from, profiles: ["core/1.0", "review/1.0"] }});
  assert.equal(setProfiles("human:alice").error?.code, -32011);
  assert.ok("result" in setProfiles("human:admin"));
});

test("reads are transport-delegated by default", () => {
  const c = memberWs();
  assert.ok("result" in c.dispatch({ jsonrpc: "2.0", id: "r", method: "audit.read", params: { workspace: "w" }}));
  assert.ok("result" in c.dispatch({ jsonrpc: "2.0", id: "d", method: "workspace.describe", params: { workspace: "w" }}));
});

test("reads gate on requireReadMembership", () => {
  const c = memberWs({ requireReadMembership: true });
  const read = (from?: string) => c.dispatch({ jsonrpc: "2.0", id: "r", method: "audit.read",
    params: from ? { workspace: "w", from } : { workspace: "w" }});
  assert.equal(read("human:stranger").error?.code, -32011);
  assert.ok("result" in read("agent:bot"));
});
