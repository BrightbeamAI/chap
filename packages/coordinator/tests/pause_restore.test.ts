import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function setup() {
  const c = new Coordinator({
    deterministicIds: true,
    defaultProfiles: ["core/1.0", "review/1.0", "control/1.0"],
  });
  const s = (m: string, from: string, p: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m, params: { workspace: "w", from, ...p } } as never);
  s("workspace.create", "human:a", { profiles: ["core/1.0", "review/1.0", "control/1.0"] });
  s("participant.join", "human:a", { type: "human" });
  s("participant.join", "agent:b", { type: "agent" });
  const tid = s("task.create", "human:a", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  return { c, s, tid };
}

test("resume restores review_requested and the review stays actionable", () => {
  const { c, s, tid } = setup();
  s("review.request", "agent:b", { task_id: tid, artefact: { text: "draft" }, to: "human:a" });
  assert.equal((c.workspaces.get("w") as any).tasks.get(tid).state, "review_requested");
  s("control.pause", "human:a", { task_id: tid, reason: "hold" });
  assert.equal((c.workspaces.get("w") as any).tasks.get(tid).state, "paused");

  const r = s("control.resume", "human:a", { task_id: tid });
  assert.equal(r.result.state, "review_requested");

  const decided = s("decide.approve", "human:a", { task_id: tid, comment: "ok", rationale: "ok" });
  assert.ok(decided.result);
  assert.equal((c.workspaces.get("w") as any).tasks.get(tid).state, "completed");
});

test("resume restores in_progress", () => {
  const { c, s, tid } = setup();
  s("task.update", "agent:b", { task_id: tid, state: "in_progress" });
  s("control.pause", "human:a", { task_id: tid, reason: "hold" });
  const r = s("control.resume", "human:a", { task_id: tid });
  assert.equal(r.result.state, "in_progress");
});

test("a repeated pause resumes in one call", () => {
  const { c, s, tid } = setup();
  s("review.request", "agent:b", { task_id: tid, artefact: { text: "draft" }, to: "human:a" });
  s("control.pause", "human:a", { task_id: tid, reason: "hold" });
  s("control.pause", "human:a", { task_id: tid, reason: "hold again" });
  const ws: any = c.workspaces.get("w");
  assert.equal(ws.tasks.get(tid).paused_from, "review_requested");

  const r = s("control.resume", "human:a", { task_id: tid });
  assert.equal(r.result.state, "review_requested");
  assert.equal(ws.tasks.get(tid).state, "review_requested");
  assert.equal(ws.tasks.get(tid).paused, false);
});

test("paused_from survives a snapshot round-trip", () => {
  const { c, s, tid } = setup();
  s("review.request", "agent:b", { task_id: tid, artefact: { text: "draft" }, to: "human:a" });
  s("control.pause", "human:a", { task_id: tid, reason: "hold" });
  const snap = JSON.parse(JSON.stringify(c.snapshot()));
  const c2 = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0", "control/1.0"] });
  c2.restore(snap);
  assert.equal((c2.workspaces.get("w") as any).tasks.get(tid).paused_from, "review_requested");
});
