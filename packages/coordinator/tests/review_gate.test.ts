/**
 * Two ways round the review gate, both closed.
 *
 * `task.complete` opens a review rather than completing when a task requires
 * one. `task.update` reaches `completed` by another route and carried no such
 * check, so a required review could be skipped entirely: the task finished, no
 * artefact was recorded, and no `decide.*` appeared on the chain.
 *
 * Separately, an open review may be widened with the same artefact. The rule
 * was compared after the default had been applied, so omitting `rule` on the
 * second request counted as changing it and the documented widening path was
 * refused.
 *
 * Mirrors packages/coordinator-py/tests/test_review_gate.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { E } from "../src/jsonrpc.js";

const PROFILES = ["core/1.0", "review/1.0"];
const ARTEFACT = { draft: "text" };

function ready() {
  const c = new Coordinator({ defaultProfiles: PROFILES, deterministicIds: true });
  const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:b") =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create", { profiles: PROFILES });
  for (const [uri, type] of [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"]])
    send("participant.join", { type }, uri);
  return { c, send };
}

const task = (c: any, id: string) => c.workspaces.get("w").tasks.get(id);

function requiredTask(send: any, state = "in_progress") {
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b",
                                   review_required: true }).result.task_id;
  if (state === "in_progress") send("task.update", { task_id: id, state: "in_progress" });
  return id;
}

// -- task.update must not finish work that needs reviewing -----------------

test("task.update cannot complete a task that requires review", () => {
  const { c, send } = ready();
  const id = requiredTask(send);
  const before = send("audit.read", {}, "human:a").result.entries.length;

  const r = send("task.update", { task_id: id, state: "completed" });

  assert.notEqual(r.error, undefined, "a required review was skipped by task.update");
  assert.equal(r.error.code, E.PARAMS);
  assert.ok(r.error.message.includes("task.complete"), "the message should name the way through");
  assert.equal(task(c, id).state, "in_progress");
  assert.equal(task(c, id).output, undefined);
  assert.equal(task(c, id).review, undefined);
  assert.equal(send("audit.read", {}, "human:a").result.entries.length, before);
});

test("from created the transition is illegal before the gate is reached", () => {
  // Two refusals for the same call. The lifecycle table rejects
  // created -> completed on its own, so the gate is defence in depth.
  const { c, send } = ready();
  const id = requiredTask(send, "created");
  const r = send("task.update", { task_id: id, state: "completed" });
  assert.equal(r.error.code, E.PARAMS);
  assert.equal(task(c, id).state, "created");
});

test("the route through is complete then decide", () => {
  const { c, send } = ready();
  const id = requiredTask(send);
  assert.equal(send("task.complete", { task_id: id, output: ARTEFACT }).result.state,
               "review_requested");
  assert.equal(send("decide.approve", { task_id: id, comment: "ok", rationale: "ok" },
                    "human:a").result.state, "completed");
  assert.deepEqual(task(c, id).output, ARTEFACT);
});

test("task.update still completes a task that needs no review", () => {
  const { c, send } = ready();
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("task.update", { task_id: id, state: "in_progress" });
  assert.equal(send("task.update", { task_id: id, state: "completed" }).error, undefined);
  assert.equal(task(c, id).state, "completed");
});

for (const state of ["in_progress", "declined", "paused"]) {
  test(`task.update to ${state} is unaffected`, () => {
    const { c, send } = ready();
    const id = requiredTask(send, "created");
    assert.equal(send("task.update", { task_id: id, state }).error, undefined, state);
    assert.equal(task(c, id).state, state);
  });
}

// -- widening an open review -----------------------------------------------

test("a reviewer can be added without restating the rule", () => {
  const { c, send } = ready();
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("review.request", { task_id: id, artefact: ARTEFACT, to: ["human:a"], rule: "all_approve" });

  const r = send("review.request", { task_id: id, artefact: ARTEFACT, to: ["human:c"] });

  assert.equal(r.error, undefined, JSON.stringify(r.error));
  assert.equal(r.result.amended, true);
  assert.deepEqual([...task(c, id).review.requested_to].sort(), ["human:a", "human:c"]);
  assert.equal(task(c, id).review.rule, "all_approve", "the rule must not have moved");
});

test("restating the same rule is still accepted", () => {
  const { send } = ready();
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("review.request", { task_id: id, artefact: ARTEFACT, to: ["human:a"], rule: "all_approve" });
  const r = send("review.request", { task_id: id, artefact: ARTEFACT, to: ["human:c"],
                                     rule: "all_approve" });
  assert.equal(r.result.amended, true);
});

test("a different rule is still refused", () => {
  const { c, send } = ready();
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("review.request", { task_id: id, artefact: ARTEFACT, to: ["human:a"], rule: "all_approve" });

  const r = send("review.request", { task_id: id, artefact: ARTEFACT, to: ["human:c"],
                                     rule: "any_one_approves" });

  assert.equal(r.error.code, E.REVIEW_ALREADY_OPEN);
  assert.equal(task(c, id).review.rule, "all_approve");
  assert.deepEqual(task(c, id).review.requested_to, ["human:a"], "the refusal changed nothing");
});
