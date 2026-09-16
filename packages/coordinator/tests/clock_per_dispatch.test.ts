import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function run() {
  const c = new Coordinator({
    defaultProfiles: ["core/1.0", "review/1.0"],
    deterministicIds: true, deterministicClock: true, enableChain: true,
  } as any);
  const s = (m: string, from = "human:a", p: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m, params: { workspace: "w", from, ...p } } as never);
  s("workspace.create", undefined, { profiles: ["core/1.0", "review/1.0"] });
  s("participant.join", undefined, { type: "human" });
  s("participant.join", "agent:b", { type: "agent" });
  const tid = s("task.create", undefined, { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  s("task.update", "agent:b", { task_id: tid, state: "in_progress" });
  return { c, tid };
}

test("arrived advances one step per dispatch", () => {
  const { c } = run();
  const stamps = (c.workspaces.get("w") as any).audit.map((e: any) => Date.parse(e.arrived));
  const diffs = stamps.slice(1).map((v: number, i: number) => v - stamps[i]);
  assert.deepEqual(diffs, diffs.map(() => 1000), JSON.stringify(stamps));
});

test("now is frozen within a dispatch", () => {
  const { c, tid } = run();
  const ws: any = c.workspaces.get("w");
  assert.equal(ws.tasks.get(tid).updated_at, ws.audit[ws.audit.length - 1].arrived);
});

test("now() outside dispatch still advances", () => {
  const c = new Coordinator({ deterministicClock: true } as any);
  assert.notEqual(c.now(), c.now());
});
