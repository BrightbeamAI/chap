/**
 * SPECIFICATION §10.1 in the reference server: a member's refused call is
 * recorded under `request` with its `outcome`, and no other refusal is.
 *
 * The reference shares no code with the coordinators, so these cases mirror
 * the ones the coordinators are held to.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { dispatch } from "./server.ts";

let n = 0;
const send = (method: string, params: Record<string, unknown>, id = `t${++n}`): any =>
  dispatch({ jsonrpc: "2.0", id, method, params } as never);

const log = (ws: string, filter?: Record<string, unknown>): any[] =>
  send("audit.read", { workspace: ws, from: "human:a", ...(filter ? { filter } : {}) }).result.entries;

/** A review addressed to human:a, with human:c a member it was not addressed to. */
function openReview(ws: string): string {
  send("participant.join", { workspace: ws, from: "human:a", type: "human" });
  send("participant.join", { workspace: ws, from: "human:c", type: "human" });
  send("participant.join", { workspace: ws, from: "agent:b", type: "agent" });
  const task = send("task.create", { workspace: ws, from: "human:a", kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("task.update", { workspace: ws, from: "agent:b", task_id: task, state: "in_progress" });
  send("review.request", { workspace: ws, from: "agent:b", task_id: task, to: ["human:a"], artefact: { body: "x" } });
  return task;
}

test("a member's refused decision is recorded under request, with its outcome", () => {
  const task = openReview("r1");
  const before = log("r1").length;
  const r = send("decide.approve", { workspace: "r1", from: "human:c", task_id: task }, "late");
  assert.equal(r.error.code, -32011);

  const entries = log("r1");
  assert.equal(entries.length, before + 1);
  const entry = entries.at(-1);
  assert.deepEqual(Object.keys(entry), ["seq", "arrived", "request", "outcome"]);
  assert.equal(entry.request.id, "late");
  assert.equal(entry.request.params.from, "human:c");
  assert.deepEqual(entry.outcome, { status: "refused", code: -32011 });
  assert.equal(send("workspace.describe", { workspace: "r1" }).result.audit_count, before + 1);
});

test("a refusal that is not a governed attempt is not recorded", () => {
  const task = openReview("r2");
  const before = log("r2").length;
  // A sender who never joined.
  assert.equal(send("decide.approve", { workspace: "r2", from: "human:ghost", task_id: task }).error.code, -32011);
  // Invalid parameters.
  assert.equal(send("decide.approve", { workspace: "r2", from: "human:c", task_id: "tsk_none" }).error.code, -32602);
  // A method this server does not implement.
  assert.equal(send("control.pause", { workspace: "r2", from: "human:a" }).error.code, -32601);
  assert.equal(log("r2").length, before);
});

test("a read is not recorded, accepted or refused", () => {
  openReview("r3");
  const before = log("r3").length;
  send("workspace.describe", { workspace: "r3", from: "human:a" });
  send("audit.read", { workspace: "r3", from: "human:a", filter: { outcome: "neither" } });
  assert.equal(log("r3").length, before);
});

test("a request identical to a recorded refusal is answered with it, and not recorded again", () => {
  const task = openReview("r4");
  const params = { workspace: "r4", from: "human:c", task_id: task };
  const first = send("decide.approve", params, "same");
  assert.equal(first.error.code, -32011);
  const seq = log("r4").at(-1).seq;

  // The review is re-addressed to human:c, but the identical request is not
  // evaluated again.
  send("review.request", { workspace: "r4", from: "agent:b", task_id: task, to: ["human:c"], artefact: { body: "x" } });
  const again = send("decide.approve", params, "same");
  assert.deepEqual(again.error, {
    code: -32011,
    message: `Refused at seq ${seq}; a refused request is not evaluated again`,
    data: { refused_at_seq: seq },
  });
  assert.equal(log("r4", { outcome: "refused" }).length, 1);

  // A retry under a new id is a new request, and is evaluated.
  assert.equal(send("decide.approve", params, "retry").result.state, "completed");
});

test("filter.outcome selects accepted calls or recorded refusals", () => {
  const task = openReview("r5");
  send("decide.approve", { workspace: "r5", from: "human:c", task_id: task });
  const all = log("r5");
  const accepted = log("r5", { outcome: "accepted" });
  const refused = log("r5", { outcome: "refused" });
  assert.equal(refused.length, 1);
  assert.ok(accepted.every((e: any) => e.envelope && !e.outcome));
  assert.equal(accepted.length + refused.length, all.length);
  // The other filters read the call under either key.
  assert.equal(log("r5", { from: "human:c", method: "decide.approve" }).length, 1);
  assert.equal(log("r5", { outcome: null }).length, all.length);
  assert.equal(send("audit.read", { workspace: "r5", filter: { outcome: "both" } }).error.code, -32602);
});
