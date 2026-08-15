/**
 * Regression: escalate.raise requires the caller to be a workspace member and
 * refuses a terminal task. Per SPECIFICATION.md the transition is "any
 * non-terminal -> escalated", with terminal states completed/cancelled/superseded.
 * Guards the 0.2.9 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { E } from "../src/jsonrpc.ts";

function ready() {
  const c = new Coordinator({ deterministicIds: true });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  const tid = s("task.create", { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  return { s, tid };
}

test("a non-member cannot escalate", () => {
  const { s, tid } = ready();
  const r = s("escalate.raise", { workspace: "w", from: "ghost:x", original_task_id: tid, new_task: { assignee: "agent:b" } });
  assert.equal(r.error.code, E.NOT_AUTHORISED);
});

test("a completed task cannot be escalated", () => {
  const { s, tid } = ready();
  s("task.update", { workspace: "w", from: "agent:b", task_id: tid, state: "in_progress" });
  s("task.complete", { workspace: "w", from: "agent:b", task_id: tid, output: {} });
  const r = s("escalate.raise", { workspace: "w", from: "human:a", original_task_id: tid, new_task: { assignee: "agent:b" } });
  assert.ok(r.error.message.includes("terminal"));
});

test("a member can escalate an active task", () => {
  const { s, tid } = ready();
  const r = s("escalate.raise", { workspace: "w", from: "human:a", original_task_id: tid, new_task: { assignee: "agent:b" } });
  assert.equal(r.result.escalated_from, tid);
});
