/**
 * Regression: task.create with a repeated idempotency_key returns the original
 * task and records no duplicate. Mirrors test_task_idempotency.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

function ws() {
  const c = new Coordinator({});
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create", params: { workspace: "w" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "agent:bot", type: "agent" }});
  return c;
}

function create(c: Coordinator, key?: string) {
  const params: Record<string, unknown> = { workspace: "w", from: "agent:bot", kind: "k", input: {}, assignee: "agent:bot" };
  if (key !== undefined) params.idempotency_key = key;
  return c.dispatch({ jsonrpc: "2.0", id: "t", method: "task.create", params });
}

test("repeated idempotency_key returns the same task without a duplicate", () => {
  const c = ws();
  const r1 = create(c, "cap-42");
  const r2 = create(c, "cap-42");
  assert.equal((r1.result as { task_id: string }).task_id, (r2.result as { task_id: string }).task_id);

  const w = c.workspaces.get("w")!;
  assert.equal(w.tasks.size, 1);
  const creates = w.audit.filter(e => e.envelope.method === "task.create");
  assert.equal(creates.length, 1);
});

test("no key creates distinct tasks", () => {
  const c = ws();
  const a = create(c);
  const b = create(c);
  assert.notEqual((a.result as { task_id: string }).task_id, (b.result as { task_id: string }).task_id);
  assert.equal(c.workspaces.get("w")!.tasks.size, 2);
});
