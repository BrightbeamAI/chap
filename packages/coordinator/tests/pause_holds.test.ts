import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function pausedTask() {
  const c = new Coordinator({ defaultProfiles: ["core/1.0", "control/1.0"] });
  const s = (method: string, from: string, params: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params: { workspace: "w", from, ...params } } as never);
  s("workspace.create", "human:gov", { profiles: ["core/1.0", "control/1.0"] });
  s("participant.join", "human:gov", { type: "human" });
  s("participant.join", "agent:worker", { type: "agent" });
  const tid = s("task.create", "human:gov", { kind: "k", input: {}, assignee: "agent:worker" }).result.task_id;
  s("task.update", "agent:worker", { task_id: tid, state: "in_progress" });
  s("control.pause", "human:gov", { task_id: tid, reason: "hold" });
  assert.equal((c.workspaces.get("w") as any).tasks.get(tid).state, "paused");
  return { c, s, tid };
}

test("a paused task cannot be resumed with task.update", () => {
  const { c, s, tid } = pausedTask();
  const r = s("task.update", "agent:worker", { task_id: tid, state: "in_progress" });
  assert.equal(r.error.code, -32602);
  assert.equal((c.workspaces.get("w") as any).tasks.get(tid).state, "paused");
});

test("control.resume is the only way out of a pause", () => {
  const { c, s, tid } = pausedTask();
  const r = s("control.resume", "human:gov", { task_id: tid });
  assert.ok(r.result);
  assert.equal((c.workspaces.get("w") as any).tasks.get(tid).state, "in_progress");
});

test("a paused task may still be cancelled", () => {
  const { c, s, tid } = pausedTask();
  const r = s("task.update", "agent:worker", { task_id: tid, state: "cancelled" });
  assert.ok(r.result);
  assert.equal((c.workspaces.get("w") as any).tasks.get(tid).state, "cancelled");
});
