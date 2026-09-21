/**
 * Regression (#157): task.update reaches no paused state.
 *
 * #142 removed paused -> in_progress from task.update, leaving control.resume
 * as the only way out of a pause while pause itself stayed reachable from
 * Core. A workspace advertising core/1.0 alone could then hold a task it had
 * no advertised method to lift.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

const CORE_ONLY = ["core/1.0", "review/1.0"];
const WITH_CONTROL = [...CORE_ONLY, "control/1.0"];

function ready(profiles: string[]) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                              defaultProfiles: profiles } as never);
  const send = (method: string, params: Record<string, unknown>, from = "human:a"): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from, ...params } } as never);
  send("workspace.create", { profiles });
  for (const [uri, type] of [["human:a", "human"], ["agent:b", "agent"]])
    send("participant.join", { type }, uri);
  return { c, send };
}

for (const state of ["created", "in_progress"]) {
  test(`task.update cannot pause a task from ${state}`, () => {
    const { c, send } = ready(CORE_ONLY);
    const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
    if (state === "in_progress") send("task.update", { task_id: id, state }, "agent:b");

    const refused = send("task.update", { task_id: id, state: "paused" }, "agent:b");

    assert.ok(refused.error);
    assert.equal(refused.error.message, `Illegal transition ${state} -> paused`);
    assert.equal((c.workspaces.get("w") as any).tasks.get(id).state, state);
  });
}

test("control.pause and control.resume are the pair", () => {
  const { c, send } = ready(WITH_CONTROL);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("task.update", { task_id: id, state: "in_progress" }, "agent:b");

  assert.equal(send("control.pause", { task_id: id, reason: "hold" }).error, undefined);
  assert.equal((c.workspaces.get("w") as any).tasks.get(id).state, "paused");
  assert.equal(send("control.resume", { task_id: id }).error, undefined);
  assert.equal((c.workspaces.get("w") as any).tasks.get(id).state, "in_progress");
});

test("a paused task can still be cancelled through task.update", () => {
  const { c, send } = ready(WITH_CONTROL);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("control.pause", { task_id: id, reason: "hold" });
  assert.equal(send("task.update", { task_id: id, state: "cancelled" }).error, undefined);
  assert.equal((c.workspaces.get("w") as any).tasks.get(id).state, "cancelled");
});
