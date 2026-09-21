/**
 * Regression (#153): control.supersede applies the participant-paused check.
 *
 * task.create refuses a paused assignee. Superseding creates a task too, and
 * skipped the check, so a successor could be handed to a participant whose
 * work control.pause had stopped.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

const PROFILES = ["core/1.0", "review/1.0", "control/1.0"];

function ready() {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                              defaultProfiles: PROFILES } as never);
  const s = (method: string, params: Record<string, unknown>, from = "human:a"): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from, ...params } } as never);
  s("workspace.create", { profiles: PROFILES });
  for (const [uri, type] of [["human:a", "human"], ["agent:b", "agent"], ["agent:c", "agent"]])
    s("participant.join", { type }, uri);
  return { c, s };
}

test("a successor cannot be handed to a paused participant", () => {
  const { c, s } = ready();
  const taskId = s("task.create", { kind: "draft", input: {}, assignee: "agent:b" }).result.task_id;
  s("control.pause", { scope: "participant", participant_uri: "agent:c", reason: "hold" });

  const refused = s("control.supersede", {
    task_id: taskId, reason: "redo",
    successor_task: { kind: "draft", input: {}, assignee: "agent:c" },
  });
  assert.ok(refused.error);
  assert.match(refused.error.message, /paused/);
  // The same refusal task.create gives, so the two agree.
  const direct = s("task.create", { kind: "draft", input: {}, assignee: "agent:c" });
  assert.equal(direct.error.code, refused.error.code);
  const ws = c.workspaces.get("w") as any;
  assert.equal(ws.tasks.get(taskId).state, "created");
  assert.equal(ws.tasks.size, 1);
});

test("superseding to an active participant still works", () => {
  const { c, s } = ready();
  const taskId = s("task.create", { kind: "draft", input: {}, assignee: "agent:b" }).result.task_id;
  const ok = s("control.supersede", {
    task_id: taskId, reason: "redo",
    successor_task: { kind: "draft", input: {}, assignee: "agent:c" },
  });
  assert.ok(ok.result);
  assert.equal((c.workspaces.get("w") as any).tasks.get(taskId).state, "superseded");
});
