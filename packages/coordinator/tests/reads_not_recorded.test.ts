/**
 * Regression: read-only methods (audit.read, workspace.describe,
 * audit.verify_chain, audit.verify_receipt) do not append to the audit chain.
 * Recording a read grew and re-linked the chain each time it was inspected.
 * Guards the 0.2.7 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function ready(opts: Record<string, unknown> = {}) {
  const c = new Coordinator({ deterministicIds: true, ...opts });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  return { c, s };
}

test("reads do not grow the chain", () => {
  const { c, s } = ready({ enableChain: true });
  const n0 = (c.workspaces.get("w") as any).audit.length;
  for (let i = 0; i < 3; i++) s("audit.read", { workspace: "w" });
  s("workspace.describe", { workspace: "w" });
  s("audit.verify_chain", { workspace: "w" });
  assert.equal((c.workspaces.get("w") as any).audit.length, n0);
});

test("writes are still recorded", () => {
  const { c, s } = ready();
  const n0 = (c.workspaces.get("w") as any).audit.length;
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  assert.equal((c.workspaces.get("w") as any).audit.length, n0 + 1);
});
