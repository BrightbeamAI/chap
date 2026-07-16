/**
 * Tamper-evidence regression tests for audit.verify_chain.
 *
 * The chain must detect tampering of any entry, including the last, and must
 * not let an entry opt out of verification by dropping its prev_hash.
 * Regression guard for the two bugs fixed in 0.2.7.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function fresh() {
  const c = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"] });
  const s = (method: string, params: unknown) =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w", profiles: ["core/1.0", "review/1.0", "audit-scitt/1.0"] });
  s("participant.join", { workspace: "w", from: "agent:bot", type: "agent" });
  s("task.create", { workspace: "w", from: "agent:bot", task: "t1", intent: "first" });
  s("task.create", { workspace: "w", from: "agent:bot", task: "t2", intent: "last" });
  return { c, s };
}

test("legit chain verifies", () => {
  const { s } = fresh();
  assert.ok("result" in (s("audit.verify_chain", { workspace: "w" }) as object));
});

test("tampering a middle entry is caught", () => {
  const { c, s } = fresh();
  (c.workspaces.get("w") as any).audit[1].envelope.params.intent = "TAMPERED";
  assert.ok("error" in (s("audit.verify_chain", { workspace: "w" }) as object));
});

test("tampering the last entry is caught via head check", () => {
  const { c, s } = fresh();
  const ws = c.workspaces.get("w") as any;
  ws.audit[ws.audit.length - 1].envelope.params.intent = "TAMPERED";
  assert.ok("error" in (s("audit.verify_chain", { workspace: "w" }) as object));
});

test("dropping prev_hash on a tampered last entry does not bypass the check", () => {
  const { c, s } = fresh();
  const ws = c.workspaces.get("w") as any;
  ws.audit[ws.audit.length - 1].envelope.params.intent = "TAMPERED";
  delete ws.audit[ws.audit.length - 1].prev_hash;
  assert.ok("error" in (s("audit.verify_chain", { workspace: "w" }) as object));
});
