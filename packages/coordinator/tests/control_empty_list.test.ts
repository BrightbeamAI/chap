import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

const PROFILES = ["core/1.0", "review/1.0", "control/1.0"];

function ready() {
  const c = new Coordinator({ defaultProfiles: PROFILES });
  const s = (m: string, params: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m,
      params: { workspace: "w", from: "human:a", ...params } } as never);
  s("workspace.create", { profiles: PROFILES });
  s("participant.join", { type: "human" });
  return { c, s };
}

function snap(c: Coordinator, sid: string): any {
  return (c.workspaces.get("w") as any).snapshots.get(sid);
}

test("snapshot with explicit empty include captures nothing", () => {
  const { c, s } = ready();
  const sid = s("control.snapshot", { include: [] }).result.snapshot_artefact_id;
  assert.deepEqual(snap(c, sid).include, []);
  assert.deepEqual(snap(c, sid).state, {});
});

test("snapshot with omitted include uses the defaults", () => {
  const { c, s } = ready();
  const sid = s("control.snapshot").result.snapshot_artefact_id;
  assert.deepEqual([...snap(c, sid).include].sort(),
    ["members", "mode_ceiling", "open_tasks"]);
});

test("rollback with explicit empty what_to_restore restores nothing", () => {
  const { s } = ready();
  const sid = s("control.snapshot", { include: ["members", "mode_ceiling"] }).result.snapshot_artefact_id;
  const r = s("control.rollback", { to_snapshot_artefact_id: sid, what_to_restore: [] });
  assert.deepEqual(r.result.restored, []);
});

test("rollback with omitted what_to_restore restores the snapshot's include", () => {
  const { s } = ready();
  const sid = s("control.snapshot", { include: ["members", "mode_ceiling"] }).result.snapshot_artefact_id;
  const r = s("control.rollback", { to_snapshot_artefact_id: sid });
  assert.deepEqual([...r.result.restored].sort(), ["members", "mode_ceiling"]);
});
