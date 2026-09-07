/**
 * An implicit review is addressed to people, not to whichever member happens to
 * be left over.
 *
 * 0.2.12 made `task.complete` open a review when the task requires one,
 * addressed to the members who are neither the completer nor the assignee. That
 * excludes the producer, which was the point, but in a workspace with more than
 * one agent it also makes the *other agent* an eligible reviewer. It could then
 * approve, and the chain would carry a `decide.approve` that reads as human
 * oversight and is not, which is the failure `review_required` exists to
 * prevent.
 *
 * The implicit review now addresses the human members other than the completer
 * and the assignee. Where there are none the completion is refused rather than
 * opening a review no person can decide. An explicit `review.request` keeps
 * whatever `to` it was given.
 *
 * Mirrors packages/coordinator-py/tests/test_implicit_review_reviewers.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { E } from "../src/jsonrpc.js";

const PROFILES = ["core/1.0", "review/1.0"];
const DRAFT = { text: "draft" };

const ONE_HUMAN_TWO_AGENTS: [string, string][] = [
  ["human:you", "human"], ["agent:drafter", "agent"], ["agent:other", "agent"],
];

function ws(members: [string, string][], profiles = PROFILES) {
  const c = new Coordinator({ defaultProfiles: profiles, deterministicIds: true });
  const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:drafter") =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create", { profiles });
  for (const [uri, type] of members) send("participant.join", { type }, uri);
  return { c, send };
}

const task = (c: any, id: string) => c.workspaces.get("w").tasks.get(id);

test("the implicit review is addressed to the human", () => {
  const { c, send } = ws(ONE_HUMAN_TWO_AGENTS);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter",
                                   review_required: true }).result.task_id;
  send("task.complete", { task_id: id, output: DRAFT });
  assert.deepEqual(task(c, id).review.requested_to, ["human:you"]);
});

test("another agent cannot approve the first agent's work", () => {
  const { c, send } = ws(ONE_HUMAN_TWO_AGENTS);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter",
                                   review_required: true }).result.task_id;
  send("task.complete", { task_id: id, output: DRAFT });

  const r = send("decide.approve", { task_id: id, comment: "looks fine", rationale: "looks fine" },
                 "agent:other");

  assert.notEqual(r.error, undefined, "an agent approved another agent's work");
  assert.equal(r.error.code, E.NOT_AUTHORISED);
  assert.equal(task(c, id).state, "review_requested");
  assert.equal(task(c, id).review.decisions.length, 0);
});

test("the human can still approve", () => {
  const { c, send } = ws(ONE_HUMAN_TWO_AGENTS);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter",
                                   review_required: true }).result.task_id;
  send("task.complete", { task_id: id, output: DRAFT });
  const r = send("decide.approve", { task_id: id, comment: "ok", rationale: "ok" }, "human:you");
  assert.equal(r.result.state, "completed");
  assert.deepEqual(task(c, id).output, DRAFT);
});

test("every human is addressed", () => {
  const { c, send } = ws([["human:you", "human"], ["human:them", "human"],
                          ["agent:drafter", "agent"], ["agent:other", "agent"]]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter",
                                   review_required: true }).result.task_id;
  send("task.complete", { task_id: id, output: DRAFT });
  assert.deepEqual([...task(c, id).review.requested_to].sort(), ["human:them", "human:you"]);
});

test("the completer is excluded even when human", () => {
  const { c, send } = ws([["human:you", "human"], ["human:them", "human"],
                          ["agent:drafter", "agent"]]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter",
                                   review_required: true }).result.task_id;
  send("task.complete", { task_id: id, output: DRAFT }, "human:you");
  assert.deepEqual(task(c, id).review.requested_to, ["human:them"]);
});

test("the assignee is excluded even when human", () => {
  const { c, send } = ws([["human:you", "human"], ["human:them", "human"],
                          ["agent:drafter", "agent"]]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "human:them",
                                   review_required: true }).result.task_id;
  send("task.complete", { task_id: id, output: DRAFT }, "agent:drafter");
  assert.deepEqual(task(c, id).review.requested_to, ["human:you"]);
});

test("a workspace with no human refuses the completion", () => {
  // Fail closed. Opening a review no person can decide is worse than refusing:
  // it produces an audit trail that looks supervised.
  const { c, send } = ws([["agent:drafter", "agent"], ["agent:other", "agent"]]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter",
                                   review_required: true }).result.task_id;
  const before = send("audit.read").result.entries.length;

  const r = send("task.complete", { task_id: id, output: DRAFT });

  assert.notEqual(r.error, undefined);
  assert.equal(r.error.code, E.NOT_AUTHORISED);
  assert.ok(r.error.message.includes("human"), r.error.message);
  assert.equal(task(c, id).state, "created", "the refused completion still moved the task");
  assert.equal(task(c, id).output, undefined, "unreviewed output was written anyway");
  assert.equal(task(c, id).review, undefined);
  assert.equal(send("audit.read").result.entries.length, before);
});

test("the only human cannot review their own work", () => {
  // The same exclusion with the producer a person rather than an agent: the
  // agent in the workspace does not become the reviewer by default.
  const { c, send } = ws([["human:you", "human"], ["agent:drafter", "agent"]]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "human:you",
                                   review_required: true }, "human:you").result.task_id;
  const r = send("task.complete", { task_id: id, output: DRAFT }, "human:you");
  assert.equal(r.error?.code, E.NOT_AUTHORISED);
  assert.equal(task(c, id).state, "created");
});

test("a service member is not a reviewer", () => {
  const { c, send } = ws([["service:coordinator", "service"], ["agent:drafter", "agent"]]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter",
                                   review_required: true }).result.task_id;
  const r = send("task.complete", { task_id: id, output: DRAFT });
  assert.equal(r.error?.code, E.NOT_AUTHORISED);
  assert.equal(task(c, id).state, "created");
});

test("an explicit request can still address an agent", () => {
  // Agent-reviews-agent stays available when somebody asks for it on purpose.
  const { c, send } = ws(ONE_HUMAN_TWO_AGENTS);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter" }).result.task_id;
  send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:other"] });
  assert.deepEqual(task(c, id).review.requested_to, ["agent:other"]);
  const r = send("decide.approve", { task_id: id, comment: "ok", rationale: "ok" }, "agent:other");
  assert.equal(r.result.state, "completed");
});

test("an explicit request before completion is left alone", () => {
  // task.complete only computes a reviewer set when there is no review yet.
  const { c, send } = ws(ONE_HUMAN_TWO_AGENTS);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter",
                                   review_required: true }).result.task_id;
  send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:other"] });
  send("task.complete", { task_id: id, output: DRAFT });
  assert.deepEqual(task(c, id).review.requested_to, ["agent:other"]);
});

test("trial mode takes the same path", () => {
  // modes/1.0 forces review by setting review_required, so the same rule
  // applies to a trial workspace that never mentioned review_required itself.
  const { c, send } = ws(ONE_HUMAN_TWO_AGENTS, ["core/1.0", "review/1.0", "modes/1.0"]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:drafter" }).result.task_id;
  assert.equal(task(c, id).review_required, true, "trial mode should force review");
  send("task.complete", { task_id: id, output: DRAFT });
  assert.deepEqual(task(c, id).review.requested_to, ["human:you"]);
});
