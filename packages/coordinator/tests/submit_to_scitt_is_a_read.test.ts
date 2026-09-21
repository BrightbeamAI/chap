/**
 * Regression (#153): audit.submit_to_scitt does not append to the log it
 * submits. It reads the chain and sends it onward. Recording the submission
 * grew the chain and moved its head, so the receipt attested a chain one
 * entry shorter than the one the workspace then held.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

const PROFILES = ["core/1.0", "audit-scitt/1.0"];

test("submitting the chain does not extend it", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                              enableChain: true, defaultProfiles: PROFILES } as never);
  const s = (method: string, params: Record<string, unknown>): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from: "human:a", ...params } } as never);
  s("workspace.create", { profiles: PROFILES });
  s("participant.join", { type: "human" });

  const ws = c.workspaces.get("w") as any;
  const entries = ws.audit.length;
  const head = ws.chain_head;
  const result = s("audit.submit_to_scitt", {});

  assert.ok(result.result.statements.length > 0, "the submission carried the chain");
  assert.equal(ws.audit.length, entries);
  assert.equal(ws.chain_head, head);
  assert.equal(s("audit.verify_chain", {}).result.ok, true);
});
