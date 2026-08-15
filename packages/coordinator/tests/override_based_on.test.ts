/**
 * Regression: decide.override records the artefact under review as its base, not
 * a caller-supplied based_on_artefact. A fabricated "before" is ignored. Guards
 * the 0.2.9 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

test("override ignores a caller-supplied based_on_artefact", () => {
  const c = new Coordinator({ deterministicIds: true });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  const tid = s("task.create", { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  s("review.request", { workspace: "w", from: "agent:b", task_id: tid, to: ["human:a"], artefact: { real: "pending" } });
  const r = s("decide.override", {
    workspace: "w", from: "human:a", task_id: tid,
    based_on_artefact: { FORGED: "before" },
    diff: [{ op: "add", path: "/x", value: 1 }], rationale: "x",
  });
  assert.deepEqual(r.result.applied, { real: "pending", x: 1 });
  const ov = [...(c.workspaces.get("w") as any).overrides.values()].pop();
  assert.deepEqual(ov.based_on_artefact, { real: "pending" });
});
