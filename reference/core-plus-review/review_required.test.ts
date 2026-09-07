/**
 * review/1.0 §3.1 in the reference server: task.complete on a task whose
 * review is required opens a review rather than completing.
 *
 * The reference is a from-scratch reimplementation sharing no code with the
 * packages, so it can drift from the coordinators silently. These cases
 * mirror the ones the coordinators are held to.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { dispatch } from "./server.ts";

let n = 0;
const call = (method: string, params: Record<string, unknown>): any =>
  dispatch({ jsonrpc: "2.0", id: `t${++n}`, method, params } as never);

function workspace(id: string, members: [string, string][]) {
  call("workspace.create", { workspace: id, from: members[0][0] });
  for (const [uri, type] of members) {
    call("participant.join", { workspace: id, from: uri, type, role: "admin" });
  }
}

function activeTask(ws: string, from: string, assignee: string, reviewRequired?: boolean) {
  const r = call("task.create", {
    workspace: ws, from, kind: "k", input: {}, assignee,
    ...(reviewRequired === undefined ? {} : { review_required: reviewRequired }),
  });
  const id = r.result.task_id;
  call("task.update", { workspace: ws, from: assignee, task_id: id, state: "in_progress" });
  return id;
}

test("task.complete opens a review when review is required", () => {
  workspace("w1", [["human:a", "human"], ["agent:bot", "agent"]]);
  const id = activeTask("w1", "human:a", "agent:bot", true);

  const done = call("task.complete", {
    workspace: "w1", from: "agent:bot", task_id: id, output: { draft: "x" },
  });
  assert.equal(done.result.state, "review_requested",
    "a review_required task must not complete directly");

  const approved = call("decide.approve", { workspace: "w1", from: "human:a", task_id: id });
  assert.equal(approved.result.state, "completed",
    "a reviewer decision is what completes it");
});

test("task.complete still completes when review is not required", () => {
  workspace("w2", [["human:a", "human"], ["agent:bot", "agent"]]);
  const id = activeTask("w2", "human:a", "agent:bot");

  const done = call("task.complete", {
    workspace: "w2", from: "agent:bot", task_id: id, output: { d: 1 },
  });
  assert.equal(done.result.state, "completed");
});

test("the producer cannot be its own reviewer", () => {
  // Sole member, and it is both the completer and the assignee. Opening a
  // review here would produce one only its own author could approve, so the
  // completion is refused instead.
  workspace("w3", [["agent:only", "agent"]]);
  const id = activeTask("w3", "agent:only", "agent:only", true);

  const done = call("task.complete", {
    workspace: "w3", from: "agent:only", task_id: id, output: { d: 1 },
  });
  assert.equal(done.error.code, -32011);
  assert.match(done.error.message, /no eligible/);
});

test("the only human cannot review their own work", () => {
  // The same exclusion, with the producer a person rather than an agent: the
  // agent in the workspace does not become the reviewer by default.
  workspace("w3b", [["human:a", "human"], ["agent:bot", "agent"]]);
  const id = activeTask("w3b", "human:a", "human:a", true);

  const done = call("task.complete", {
    workspace: "w3b", from: "human:a", task_id: id, output: { d: 1 },
  });
  assert.equal(done.error?.code, -32011);
});

test("the implicit review excludes the completer and the assignee", () => {
  workspace("w4", [["human:a", "human"], ["human:b", "human"], ["agent:bot", "agent"]]);
  const id = activeTask("w4", "human:a", "agent:bot", true);

  call("task.complete", { workspace: "w4", from: "human:a", task_id: id, output: { d: 1 } });
  const desc = call("audit.read", { workspace: "w4", filter: { task_id: id } });
  assert.ok(desc.result, "audit read should succeed");

  // human:b is the only member who is neither the completer nor the assignee.
  const approved = call("decide.approve", { workspace: "w4", from: "human:b", task_id: id });
  assert.equal(approved.result.state, "completed");
});

test("the implicit review does not admit another agent", () => {
  // Excluding the producer is not enough on its own. With a second agent in
  // the workspace it would become an eligible reviewer, and its approval would
  // sit on the chain looking like human oversight.
  workspace("w5", [["human:a", "human"], ["agent:bot", "agent"], ["agent:other", "agent"]]);
  const id = activeTask("w5", "human:a", "agent:bot", true);

  call("task.complete", { workspace: "w5", from: "agent:bot", task_id: id, output: { d: 1 } });

  const byAgent = call("decide.approve", { workspace: "w5", from: "agent:other", task_id: id });
  assert.equal(byAgent.error?.code, -32011,
    "an agent approved another agent's work");

  const byHuman = call("decide.approve", { workspace: "w5", from: "human:a", task_id: id });
  assert.equal(byHuman.result.state, "completed",
    "the human the review was addressed to must still be able to decide");
});

test("a workspace with no human refuses a required review", () => {
  // Fail closed: opening a review no person can decide produces an audit trail
  // that looks supervised.
  workspace("w6", [["agent:bot", "agent"], ["agent:other", "agent"]]);
  const id = activeTask("w6", "agent:bot", "agent:bot", true);

  const done = call("task.complete", {
    workspace: "w6", from: "agent:bot", task_id: id, output: { d: 1 },
  });
  assert.equal(done.error?.code, -32011);
  assert.match(done.error.message, /human/);
});
