import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

function send(c: Coordinator, method: string, params: Record<string, unknown>): any {
  return c.dispatch({ jsonrpc: "2.0", id: method, method, params });
}

test("control.snapshot detaches the captured state from live and returned values", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  send(c, "workspace.create", { workspace: "w", profiles: ["core/1.0", "review/1.0", "control/1.0"] });
  send(c, "participant.join", {
    workspace: "w", from: "human:a", type: "human", scopes: ["review"],
  });
  send(c, "participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  const taskId = send(c, "task.create", {
    workspace: "w", from: "human:a", kind: "draft",
    input: { nested: ["before"] }, assignee: "agent:b",
  }).result.task_id;

  const snapshot = send(c, "control.snapshot", {
    workspace: "w", from: "human:a", include: ["members", "open_tasks"],
  }).result;
  const internal = c.workspaces.get("w")!.snapshots.get(snapshot.snapshot_artefact_id)!;
  const captured = internal.content.state as any;
  const returned = snapshot.artefact as any;

  returned.content.state.members[0].scopes.push("response");
  returned.content.state.open_tasks[0].assignee = "response";
  assert.deepEqual(captured.members[0].scopes, ["review"]);
  assert.equal(captured.open_tasks[0].assignee, "agent:b");

  const live = c.workspaces.get("w")!;
  live.members.get("human:a")!.scopes!.push("live");
  live.tasks.get(taskId)!.assignee = "live";
  assert.deepEqual(captured.members[0].scopes, ["review"]);
  assert.equal(captured.open_tasks[0].assignee, "agent:b");
});
