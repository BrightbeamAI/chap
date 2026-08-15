/**
 * Regression: a weighted_vote tally uses the weights the opener set at
 * deliberate.open, not a weight the voter puts on their own vote. A voter not
 * in the opener's map counts as 1.0. Guards the 0.2.9 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function open(rule: string, weights?: Record<string, number>) {
  const c = new Coordinator({ deterministicIds: true });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  s("participant.join", { workspace: "w", from: "human:b", type: "human" });
  const params: Record<string, unknown> = { workspace: "w", from: "human:a", to: ["human:a", "human:b"], rule };
  if (weights) params.weights = weights;
  const did = s("deliberate.open", params).result.deliberation_id;
  return { s, did };
}

test("a self-declared vote weight is ignored", () => {
  const { s, did } = open("weighted_vote:2.0");
  s("deliberate.vote", { workspace: "w", from: "human:a", deliberation_id: did, vote: "yea", weight: 999 });
  const r = s("deliberate.close", { workspace: "w", from: "human:a", deliberation_id: did });
  assert.equal(r.result.outcome, "rejected");
  assert.equal(r.result.tally.yea, 1.0);
});

test("the opener's weight is authoritative", () => {
  const { s, did } = open("weighted_vote:2.0", { "human:a": 3.0 });
  s("deliberate.vote", { workspace: "w", from: "human:a", deliberation_id: did, vote: "yea", weight: 999 });
  const r = s("deliberate.close", { workspace: "w", from: "human:a", deliberation_id: did });
  assert.equal(r.result.outcome, "approved");
  assert.equal(r.result.tally.yea, 3.0);
});
