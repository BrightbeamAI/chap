/**
 * A revised artefact starts a new review round.
 *
 * A task under review goes back to `in_progress` through `task.update`, or
 * through a `decide.reject` with `request_revision`. When `task.complete`
 * then submits a new artefact, the review opens a new round: the reviewers
 * and the rule stay, and the decisions start empty. Before, the decisions on
 * the earlier artefact were kept, so under `all_approve` or `quorum:<n>` an
 * approval of the first artefact counted towards the rule on the second. A
 * decision that names its artefact with `approved_artefact_digest` cannot be
 * counted again on another, even when a copy of it is sent again.
 *
 * Mirrors packages/coordinator-py/tests/test_review_rounds.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { contentHash } from "../src/canonical.js";
import { E } from "../src/jsonrpc.js";

const PROFILES = ["core/1.0", "review/1.0"];

function ready() {
  const c = new Coordinator({ defaultProfiles: PROFILES, deterministicIds: true });
  const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:b") =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create", { profiles: PROFILES });
  for (const [uri, type] of [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"]])
    send("participant.join", { type }, uri);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b",
                                   review_required: true }).result.task_id;
  send("task.update", { task_id: id, state: "in_progress" });
  return { c, send, id };
}

const task = (c: any, id: string) => c.workspaces.get("w").tasks.get(id);
const ok = (r: any) => { assert.equal(r.error, undefined, JSON.stringify(r.error)); return r.result; };

for (const rule of ["all_approve", "quorum:2"]) {
  test(`${rule}: an approval of the first artefact does not count for the second`, () => {
    const { c, send, id } = ready();
    ok(send("review.request", { task_id: id, to: ["human:a", "human:c"], rule,
                                artefact: { draft: "v1" } }));
    ok(send("decide.approve", { task_id: id }, "human:a"));
    assert.equal(task(c, id).state, "review_requested");

    ok(send("task.update", { task_id: id, state: "in_progress" }));
    assert.equal(ok(send("task.complete", { task_id: id, output: { draft: "v2" } })).state,
                 "review_requested");
    const review = task(c, id).review;
    assert.deepEqual(review.decisions, []);
    assert.deepEqual(review.requested_to, ["human:a", "human:c"]);
    assert.equal(review.rule, rule);

    ok(send("decide.approve", { task_id: id }, "human:c"));
    assert.equal(task(c, id).state, "review_requested",
                 "an approval of the first artefact completed the second");
    assert.equal(task(c, id).output, undefined);

    ok(send("decide.approve", { task_id: id }, "human:a"));
    assert.equal(task(c, id).state, "completed");
    assert.deepEqual(task(c, id).output, { draft: "v2" });
  });
}

test("after a reject with request_revision the new artefact starts a new round", () => {
  const { c, send, id } = ready();
  ok(send("review.request", { task_id: id, to: ["human:a", "human:c"], rule: "quorum:2",
                              artefact: { draft: "v1" } }));
  ok(send("decide.approve", { task_id: id }, "human:a"));
  ok(send("decide.reject", { task_id: id, comment: "redo", request_revision: true }, "human:c"));
  assert.equal(task(c, id).state, "in_progress");

  ok(send("task.complete", { task_id: id, output: { draft: "v2" } }));
  assert.deepEqual(task(c, id).review.decisions, []);
  ok(send("decide.approve", { task_id: id }, "human:c"));
  assert.equal(task(c, id).state, "review_requested");
  ok(send("decide.approve", { task_id: id }, "human:a"));
  assert.equal(task(c, id).state, "completed");
  assert.deepEqual(task(c, id).output, { draft: "v2" });
});

test("an implicit review keeps its reviewers in the new round", () => {
  const { c, send, id } = ready();
  ok(send("task.complete", { task_id: id, output: { draft: "v1" } }));
  const first = task(c, id).review;
  assert.deepEqual(first.requested_to, ["human:a", "human:c"]);
  ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));

  ok(send("task.complete", { task_id: id, output: { draft: "v2" } }));
  const second = task(c, id).review;
  assert.deepEqual(second.requested_to, ["human:a", "human:c"]);
  assert.equal(second.rule, "any_one_approves");
  assert.deepEqual(second.decisions, []);
  ok(send("decide.approve", { task_id: id }, "human:c"));
  assert.deepEqual(task(c, id).output, { draft: "v2" });
});

test("a reviewer who submits the new artefact is not among its reviewers", () => {
  const { c, send, id } = ready();
  ok(send("review.request", { task_id: id, to: ["human:a", "human:c"], rule: "any_one_approves",
                              artefact: { draft: "v1" } }));
  ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));
  // Any member may complete the task, so a reviewer can submit the revision.
  ok(send("task.complete", { task_id: id, output: { draft: "v2 by a" } }, "human:a"));
  assert.deepEqual(task(c, id).review.requested_to, ["human:c"]);
  assert.ok(send("decide.approve", { task_id: id }, "human:a").error, "the author approved its own artefact");
  assert.equal(task(c, id).state, "review_requested");
  ok(send("decide.approve", { task_id: id }, "human:c"));
  assert.equal(task(c, id).state, "completed");
});

test("a completion that would leave the review with no reviewer is refused", () => {
  const { c, send, id } = ready();
  ok(send("review.request", { task_id: id, to: ["human:a"], artefact: { draft: "v1" } }));
  ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));
  const r = send("task.complete", { task_id: id, output: { draft: "v2" } }, "human:a");
  assert.equal(r.error?.code, -32011);
  assert.equal(task(c, id).state, "in_progress");
  assert.deepEqual(task(c, id).review.requested_to, ["human:a"]);
});

test("the first round's decisions stay on the chain", () => {
  const { send, id } = ready();
  ok(send("review.request", { task_id: id, to: ["human:a", "human:c"], rule: "all_approve",
                              artefact: { draft: "v1" } }));
  ok(send("decide.approve", { task_id: id }, "human:a"));
  ok(send("task.update", { task_id: id, state: "in_progress" }));
  ok(send("task.complete", { task_id: id, output: { draft: "v2" } }));
  const entries = ok(send("audit.read", { filter: { task_id: id } }, "human:a")).entries;
  const approvals = entries.filter((e: any) => e.envelope?.method === "decide.approve");
  assert.equal(approvals.length, 1);
});

test("a copy of a decision that names the first artefact is refused in the new round", () => {
  const { c, send, id } = ready();
  ok(send("review.request", { task_id: id, to: ["human:a", "human:c"], rule: "all_approve",
                              artefact: { draft: "v1" } }));
  const approval = { task_id: id, approved_artefact_digest: contentHash({ draft: "v1" }) };
  ok(send("decide.approve", approval, "human:a"));
  ok(send("task.update", { task_id: id, state: "in_progress" }));
  ok(send("task.complete", { task_id: id, output: { draft: "v2" } }));

  const copy = send("decide.approve", approval, "human:a");
  assert.equal(copy.error?.code, E.SIG_ARTEFACT_DIGEST_MISMATCH);
  assert.deepEqual(task(c, id).review.decisions, []);
  assert.equal(task(c, id).state, "review_requested");
});
