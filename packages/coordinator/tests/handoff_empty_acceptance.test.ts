import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

function send(c: Coordinator, method: string, params: Record<string, unknown>): any {
  return c.dispatch({ jsonrpc: "2.0", id: method, method, params });
}

test("an explicit empty accepted_task_ids list is a no-op, not accept-all", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  send(c, "workspace.create", { workspace: "w" });
  send(c, "participant.join", { workspace: "w", from: "human:alice", type: "human" });
  send(c, "participant.join", { workspace: "w", from: "human:bob", type: "human" });
  const taskId = send(c, "task.create", {
    workspace: "w", from: "human:alice", kind: "handoff", input: {},
    assignee: "human:alice",
  }).result.task_id;
  const handoffId = send(c, "handoff.propose", {
    workspace: "w", from: "human:alice", to: "human:bob",
    tasks: [{ task_id: taskId }],
  }).result.handoff_id;

  const result = send(c, "handoff.accept", {
    workspace: "w", from: "human:bob", handoff_id: handoffId,
    accepted_task_ids: [],
  });
  assert.deepEqual(result.result.task_ids, []);
  assert.equal(c.workspaces.get("w")!.tasks.get(taskId)!.assignee, "human:alice");
});
