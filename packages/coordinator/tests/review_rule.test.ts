/**
 * The workspace's review rule (SPECIFICATION 6.1.1, review profile 2.1).
 *
 * An author may not open a review of their own review-required task, so a
 * review opens on task.complete. A workspace created with review_rule opens
 * every such review under that rule: an agent cannot ask for fewer
 * approvals than the workspace sets. Mirrors test_review_rule.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { MemoryStore } from "../src/storage/store.js";

function ready(c = new Coordinator({ deterministicIds: true, deterministicClock: true })) {
  let n = 0;
  const call = (method: string, from: string, params: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from, ...params } });
  return { c, call };
}

function reviewed(call: ReturnType<typeof ready>["call"]) {
  for (const h of ["human:a@x", "human:b@x"]) call("participant.join", h, { type: "human" });
  call("participant.join", "agent:x", { type: "agent" });
  const { task_id } = call("task.create", "agent:x", { kind: "k", assignee: "agent:x", input: {}, review_required: true }).result;
  call("task.complete", "agent:x", { task_id, output: { a: 1 } });
  return task_id as string;
}

test("a review opened on task.complete takes the workspace's rule", () => {
  const { call } = ready();
  assert.ok(call("workspace.create", "human:a@x", { review_rule: "quorum:2" }).result);
  assert.equal(call("workspace.describe", "human:a@x").result.review_rule, "quorum:2");
  const task_id = reviewed(call);
  assert.equal(call("decide.approve", "human:a@x", { task_id }).result.state, "review_requested");
  assert.equal(call("decide.approve", "human:b@x", { task_id }).result.state, "completed");
});

test("without a rule the workspace opens reviews under any_one_approves", () => {
  const { call } = ready();
  call("workspace.create", "human:a@x");
  assert.equal(call("workspace.describe", "human:a@x").result.review_rule, "any_one_approves");
  const task_id = reviewed(call);
  assert.equal(call("decide.approve", "human:a@x", { task_id }).result.state, "completed");
});

test("an unknown rule is refused at workspace.create", () => {
  const { call } = ready();
  for (const rule of ["quorum:0", "quorum:x", "most", 2]) {
    assert.equal(call("workspace.create", "human:a@x", { review_rule: rule }).error.code, -32602, String(rule));
  }
});

test("the rule survives a restart", () => {
  const store = new MemoryStore();
  ready(new Coordinator({ store, deterministicIds: true, deterministicClock: true } as never))
    .call("workspace.create", "human:a@x", { review_rule: "all_approve" });
  const restarted = ready(new Coordinator({ store } as never));
  assert.equal(restarted.call("workspace.describe", "human:a@x").result.review_rule, "all_approve");
});
