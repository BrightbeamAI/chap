/**
 * Regression: every control/1.0 operation requires workspace membership.
 * Without it a non-member could defeat the governance "emergency brake".
 * Guards the 0.2.7 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function setup() {
  const c = new Coordinator({ defaultProfiles: ["core/1.0", "control/1.0"] });
  const s = (m: string, params: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m, params } as never) as { result?: unknown; error?: { code: number } };
  s("workspace.create", { workspace: "w", profiles: ["core/1.0", "control/1.0"] });
  s("participant.join", { workspace: "w", from: "human:gov", type: "human" });
  s("participant.join", { workspace: "w", from: "agent:worker", type: "agent" });
  return { c, s };
}

test("non-member cannot resume a paused workspace", () => {
  const { c, s } = setup();
  s("control.pause", { workspace: "w", from: "human:gov", scope: "workspace" });
  const r = s("control.resume", { workspace: "w", from: "human:attacker", scope: "workspace" });
  assert.ok(r.error && r.error.code === -32011);
  assert.equal((c.workspaces.get("w") as { state: string }).state, "paused");
});

test("non-member cannot raise the mode ceiling", () => {
  const { s } = setup();
  const r = s("control.set_mode_ceiling", { workspace: "w", from: "human:attacker", new_ceiling: "production" });
  assert.ok(r.error && r.error.code === -32011);
});

test("member can perform control ops", () => {
  const { s } = setup();
  s("control.pause", { workspace: "w", from: "human:gov", scope: "workspace" });
  const r = s("control.resume", { workspace: "w", from: "human:gov", scope: "workspace" });
  assert.ok("result" in r);
});
