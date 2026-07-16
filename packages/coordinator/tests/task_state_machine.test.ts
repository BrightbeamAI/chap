/**
 * Regression: task.complete is only legal from an active state (created or
 * in_progress). It must not revive a terminated task or bypass a pause.
 * Guards the 0.2.7 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function mk() {
  const c = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0", "control/1.0"] });
  const s = (m: string, params: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m, params } as never) as { result?: any; error?: { code: number } };
  s("workspace.create", { workspace: "w", profiles: ["core/1.0", "review/1.0", "control/1.0"] });
  s("participant.join", { workspace: "w", from: "agent:bot", type: "agent" });
  return s;
}
const newTask = (s: ReturnType<typeof mk>) =>
  s("task.create", { workspace: "w", from: "agent:bot", kind: "x", input: {}, assignee: "agent:bot" }).result.task_id;

test("cannot complete a cancelled task", () => {
  const s = mk();
  const tid = newTask(s);
  s("control.cancel", { workspace: "w", from: "agent:bot", task_id: tid, reason: "stop" });
  assert.ok(s("task.complete", { workspace: "w", from: "agent:bot", task_id: tid, artefact: { body: "x" } }).error);
});

test("cannot complete a paused task", () => {
  const s = mk();
  const tid = newTask(s);
  s("control.pause", { workspace: "w", from: "agent:bot", scope: "task", task_id: tid });
  assert.ok(s("task.complete", { workspace: "w", from: "agent:bot", task_id: tid, artefact: { body: "x" } }).error);
});

test("can complete an active task", () => {
  const s = mk();
  const tid = newTask(s);
  assert.ok("result" in s("task.complete", { workspace: "w", from: "agent:bot", task_id: tid, artefact: { body: "x" } }));
});

test("cannot double-complete", () => {
  const s = mk();
  const tid = newTask(s);
  s("task.complete", { workspace: "w", from: "agent:bot", task_id: tid, artefact: { body: "1" } });
  assert.ok(s("task.complete", { workspace: "w", from: "agent:bot", task_id: tid, artefact: { body: "2" } }).error);
});
