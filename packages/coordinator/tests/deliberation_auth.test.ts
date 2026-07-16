/**
 * Regression: deliberation/1.0 open/close require workspace membership, so a
 * non-member cannot open a deliberation or close it to finalize the tally
 * early. Per-voter eligibility and double-vote checks still apply.
 * Guards the 0.2.7 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function setup() {
  const c = new Coordinator({ defaultProfiles: ["core/1.0", "deliberation/1.0"] });
  const s = (m: string, params: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m, params } as never) as { result?: any; error?: { code: number } };
  s("workspace.create", { workspace: "w", profiles: ["core/1.0", "deliberation/1.0"] });
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  s("participant.join", { workspace: "w", from: "human:b", type: "human" });
  return { c, s };
}

test("non-member cannot open a deliberation", () => {
  const { s } = setup();
  const r = s("deliberate.open", { workspace: "w", from: "human:outsider", to: ["human:a", "human:b"], rule: "all_approve" });
  assert.ok(r.error && r.error.code === -32011);
});

test("non-member cannot close a deliberation", () => {
  const { s } = setup();
  const did = s("deliberate.open", { workspace: "w", from: "human:a", to: ["human:a", "human:b"], rule: "all_approve" }).result.deliberation_id;
  const r = s("deliberate.close", { workspace: "w", from: "human:outsider", deliberation_id: did });
  assert.ok(r.error && r.error.code === -32011);
});

test("member voting integrity intact", () => {
  const { s } = setup();
  const did = s("deliberate.open", { workspace: "w", from: "human:a", to: ["human:a", "human:b"], rule: "all_approve" }).result.deliberation_id;
  assert.ok("result" in s("deliberate.vote", { workspace: "w", from: "human:a", deliberation_id: did, vote: "yea" }));
  assert.ok(s("deliberate.vote", { workspace: "w", from: "human:a", deliberation_id: did, vote: "nay" }).error);
  assert.ok(s("deliberate.vote", { workspace: "w", from: "human:c", deliberation_id: did, vote: "yea" }).error);
});
