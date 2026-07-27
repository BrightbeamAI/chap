/**
 * Regression: dispatch must reject an envelope it cannot canonicalise instead
 * of letting a handler run and then throwing while linking the audit chain. A
 * non-integer or unsafe-integer number is refused with PARAMS, before any state
 * changes. Guards the 0.2.7 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { E } from "../src/jsonrpc.ts";

function ready(opts: Record<string, unknown> = {}) {
  const c = new Coordinator({ deterministicIds: true, ...opts });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  const tid = s("task.create", { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  s("task.update", { workspace: "w", from: "agent:b", task_id: tid, state: "in_progress" });
  return { c, s, tid };
}

test("a non-integer number is rejected with PARAMS", () => {
  const { s, tid } = ready();
  const r = s("task.complete", { workspace: "w", from: "agent:b", task_id: tid, output: {}, confidence: 0.86 });
  assert.equal(r.error.code, E.PARAMS);
});

test("an unsafe integer is rejected with PARAMS", () => {
  const { s, tid } = ready();
  const r = s("task.complete", { workspace: "w", from: "agent:b", task_id: tid, output: {}, confidence: 2 ** 53 });
  assert.equal(r.error.code, E.PARAMS);
});

test("the reject happens before any state change", () => {
  const { c, s, tid } = ready({ enableChain: true });
  const ws = c.workspaces.get("w") as any;
  const before = ws.audit.length;
  s("task.complete", { workspace: "w", from: "agent:b", task_id: tid, output: {}, confidence: 0.5 });
  assert.equal(ws.tasks.get(tid).state, "in_progress");
  assert.equal(ws.audit.length, before);
});

test("an integer confidence is still accepted", () => {
  const { s, tid } = ready();
  const r = s("task.complete", { workspace: "w", from: "agent:b", task_id: tid, output: {}, confidence: 1 });
  assert.equal(r.result.state, "completed");
});
