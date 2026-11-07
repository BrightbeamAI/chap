/**
 * A review a task requires ends with a decision, and handing the work on
 * does not make its author a reviewer of it (review/1.0 S3.1, S3.5).
 *
 * The authors of the work under review could end a required review with no
 * decision through control.cancel, control.supersede, or control.pause and
 * then task.update to cancelled. A person who wrote the work could hand it
 * on with escalate.raise, task.route or handoff.accept, or have it
 * superseded, and then approve the work as one of its eligible reviewers;
 * and a later round, resubmitted
 * by someone else, dropped the earlier round's authors. A request after an
 * approval named the approving reviewer as an author. Mirrors
 * packages/coordinator-py/tests/test_review_successors.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { E } from "../src/jsonrpc.js";

const PROFILES = ["core/1.0", "review/1.0", "control/1.0", "handoff/1.0", "routing/1.0"];

function ready(rule?: string) {
  const c = new Coordinator({ defaultProfiles: PROFILES, deterministicIds: true, deterministicClock: true });
  let n = 0;
  const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:b") =>
    c.dispatch({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create", { profiles: PROFILES, ...(rule ? { review_rule: rule } : {}) });
  for (const [uri, type] of [["human:a", "human"], ["human:c", "human"], ["human:h", "human"], ["agent:b", "agent"]])
    send("participant.join", { type }, uri);
  return { c, send };
}

const ok = (r: any) => { assert.equal(r.error, undefined, JSON.stringify(r.error)); return r.result; };
const task = (c: Coordinator, id: string) => c.workspaces.get("w")!.tasks.get(id)!;
const ENDS = "The authors of the work under review cannot end its review without a decision";
const AUTHOR_DECIDES = "The author of the work under review cannot decide it";

function underReview(send: ReturnType<typeof ready>["send"], assignee = "agent:b") {
  const id = ok(send("task.create", { kind: "k", input: {}, assignee, review_required: true }, "human:a")).task_id;
  ok(send("task.complete", { task_id: id, output: { body: "v1" } }, assignee));
  return id;
}

test("the authors cannot cancel or supersede a required review, or cancel it once paused", () => {
  const { c, send } = ready();
  const id = underReview(send);
  for (const [method, params] of [
    ["control.cancel", { task_id: id }],
    ["control.supersede", { task_id: id, successor_task: { kind: "k" } }],
  ] as const) {
    assert.deepEqual(send(method, params).error, { code: E.NOT_AUTHORISED, message: ENDS }, method);
  }
  ok(send("control.pause", { task_id: id }));
  assert.deepEqual(send("task.update", { task_id: id, state: "cancelled" }).error,
                   { code: E.NOT_AUTHORISED, message: ENDS });
  assert.equal(task(c, id).state, "paused");
  // Another member still pulls the brake.
  ok(send("task.update", { task_id: id, state: "cancelled" }, "human:a"));
  assert.equal(task(c, id).state, "cancelled");
});

test("authors who take the work back still cannot end its review; once a reviewer rejects it, they can", () => {
  const { c, send } = ready();
  const id = underReview(send);
  ok(send("task.update", { task_id: id, state: "in_progress" }));
  for (const [method, params] of [
    ["control.cancel", { task_id: id }],
    ["control.supersede", { task_id: id, successor_task: { kind: "k" } }],
  ] as const) {
    assert.deepEqual(send(method, params).error, { code: E.NOT_AUTHORISED, message: ENDS }, method);
  }
  ok(send("control.pause", { task_id: id }));
  assert.deepEqual(send("task.update", { task_id: id, state: "cancelled" }).error,
                   { code: E.NOT_AUTHORISED, message: ENDS });
  ok(send("control.resume", { task_id: id }, "human:a"));
  ok(send("task.complete", { task_id: id, output: { body: "v2" } }));
  ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));
  ok(send("control.cancel", { task_id: id }));
  assert.equal(task(c, id).state, "cancelled");
});

test("a person a task passes through, who never acts on it, stays among its reviewers", () => {
  const { c, send } = ready("all_approve");
  const routed = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b", review_required: true }, "human:a")).task_id;
  ok(send("task.route", { task_id: routed, candidates: ["human:c"] }));
  ok(send("task.route", { task_id: routed, candidates: ["agent:b"] }));
  assert.deepEqual(task(c, routed).prior_authors, ["agent:b"]);
  ok(send("task.complete", { task_id: routed, output: { body: "v1" } }));
  assert.deepEqual(task(c, routed).review!.requested_to, ["human:a", "human:c", "human:h"]);
  const first = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b", review_required: true }, "human:a")).task_id;
  const second = ok(send("escalate.raise", { original_task_id: first, new_task: { kind: "k", assignee: "human:c" } })).new_task_id;
  const third = ok(send("escalate.raise", { original_task_id: second, new_task: { kind: "k", assignee: "agent:b" } })).new_task_id;
  assert.deepEqual(task(c, third).prior_authors, ["agent:b"]);
  ok(send("task.complete", { task_id: third, output: { body: "v1" } }));
  assert.deepEqual(task(c, third).review!.requested_to, ["human:a", "human:c", "human:h"]);
});

test("a route of a completed task, or a note an update carries, does not hide who completed the work", () => {
  const { send } = ready();
  send("participant.join", { type: "agent" }, "agent:x");
  const routed = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "human:a")).task_id;
  ok(send("task.complete", { task_id: routed, output: { body: "v1" } }, "agent:x"));
  ok(send("task.route", { task_id: routed, candidates: ["agent:b"] }, "human:a"));
  const noted = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "human:a")).task_id;
  ok(send("task.update", { task_id: noted, state: "in_progress" }, "agent:x"));
  ok(send("task.update", { task_id: noted, state: "completed", progress_note: "override applied" }, "agent:x"));
  for (const id of [routed, noted]) {
    ok(send("review.request", { task_id: id, artefact: { body: "v1" }, to: ["agent:x"] }, "human:a"));
    assert.deepEqual(send("decide.approve", { task_id: id }, "agent:x").error,
                     { code: E.NOT_AUTHORISED, message: AUTHOR_DECIDES });
  }
});

test("a member who is not an author still cancels or supersedes a required review", () => {
  const { c, send } = ready();
  const first = underReview(send);
  ok(send("control.cancel", { task_id: first }, "human:a"));
  assert.equal(task(c, first).state, "cancelled");
  const second = underReview(send);
  const r = ok(send("control.supersede", { task_id: second, successor_task: { kind: "k" } }, "human:a"));
  assert.equal(task(c, r.new_task_id).review_required, true);
});

test("the person who wrote the work cannot approve it after escalating it", () => {
  const { c, send } = ready();
  const id = underReview(send, "human:h");
  const { new_task_id } = ok(send("escalate.raise", { original_task_id: id,
    new_task: { kind: "k", assignee: "agent:b" } }, "human:h"));
  assert.deepEqual(task(c, new_task_id).prior_authors, ["human:h"]);
  ok(send("task.complete", { task_id: new_task_id, output: { body: "v1" } }));
  assert.deepEqual(task(c, new_task_id).review!.authors, ["agent:b", "human:h"]);
  assert.ok(!task(c, new_task_id).review!.requested_to.includes("human:h"));
  assert.deepEqual(send("decide.approve", { task_id: new_task_id }, "human:h").error,
                   { code: E.NOT_AUTHORISED, message: AUTHOR_DECIDES });
  assert.equal(ok(send("decide.approve", { task_id: new_task_id }, "human:a")).state, "completed");
});

test("the person who held the work cannot approve it after routing it or handing it off", () => {
  const { c, send } = ready();
  const routed = ok(send("task.create", { kind: "k", input: {}, assignee: "human:h", review_required: true }, "human:a")).task_id;
  ok(send("task.route", { task_id: routed, candidates: ["agent:b"] }, "human:h"));
  const handed = ok(send("task.create", { kind: "k", input: {}, assignee: "human:h", review_required: true }, "human:a")).task_id;
  const { handoff_id } = ok(send("handoff.propose", { to: "agent:b", tasks: [{ task_id: handed, summary: "yours" }] }, "human:h"));
  ok(send("handoff.accept", { handoff_id }));
  for (const id of [routed, handed]) {
    assert.deepEqual(task(c, id).prior_authors, ["human:h"]);
    ok(send("task.complete", { task_id: id, output: { body: "v1" } }));
    assert.deepEqual(task(c, id).review!.authors, ["agent:b", "human:h"]);
    assert.deepEqual(send("decide.approve", { task_id: id }, "human:h").error,
                     { code: E.NOT_AUTHORISED, message: AUTHOR_DECIDES });
    assert.equal(ok(send("decide.approve", { task_id: id }, "human:a")).state, "completed");
  }
});

test("the person who wrote the work cannot approve its successor after a supersede", () => {
  const { c, send } = ready();
  const id = underReview(send, "human:h");
  const { new_task_id } = ok(send("control.supersede", { task_id: id,
    successor_task: { kind: "k", assignee: "agent:b" } }, "human:a"));
  ok(send("task.complete", { task_id: new_task_id, output: { body: "v1" } }));
  assert.deepEqual(send("decide.approve", { task_id: new_task_id }, "human:h").error,
                   { code: E.NOT_AUTHORISED, message: AUTHOR_DECIDES });
});

test("the authors of an earlier round stay authors when someone else resubmits", () => {
  const { c, send } = ready();
  const id = underReview(send, "human:h");
  ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));
  const handoff = ok(send("handoff.propose", { to: "agent:b", tasks: [{ task_id: id }] }, "human:h")).handoff_id;
  ok(send("handoff.accept", { handoff_id: handoff }));
  ok(send("task.complete", { task_id: id, output: { body: "v2" } }));
  assert.deepEqual(task(c, id).review!.authors, ["human:h", "agent:b"]);
  assert.deepEqual(send("review.request", { task_id: id, artefact: { body: "v2" }, to: ["human:h"] }, "human:a").error,
                   { code: E.NOT_AUTHORISED, message: "The author of the work under review cannot review it: human:h" });
  assert.deepEqual(send("decide.approve", { task_id: id }, "human:h").error?.code, E.NOT_AUTHORISED);
});

test("a request after an approval does not name the approving reviewer an author", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "human:a")).task_id;
  ok(send("task.complete", { task_id: id, output: { body: "v1" } }));
  ok(send("review.request", { task_id: id, artefact: { body: "v1" }, to: ["human:c"] }, "human:a"));
  assert.equal(ok(send("decide.approve", { task_id: id }, "human:c")).state, "completed");
  ok(send("review.request", { task_id: id, artefact: { body: "v1" }, to: ["human:c", "human:h"] }, "human:a"));
  assert.deepEqual(task(c, id).review!.authors, ["agent:b"]);
  assert.equal(ok(send("decide.approve", { task_id: id }, "human:c")).state, "completed");
});

test("a paused participant does not lift its own pause; another member does", () => {
  const { c, send } = ready();
  ok(send("control.pause", { scope: "participant", participant_uri: "agent:b" }, "human:a"));
  assert.deepEqual(send("control.resume", { scope: "participant", participant_uri: "agent:b" }).error,
                   { code: E.NOT_AUTHORISED, message: "A paused participant does not lift its own pause" });
  assert.equal(c.workspaces.get("w")!.members.get("agent:b")!.paused, true);
  assert.deepEqual(send("participant.leave").error,
                   { code: E.NOT_AUTHORISED, message: "A paused participant does not leave until another member lifts its pause" },
                   "leaving and joining again would lift it");
  ok(send("control.resume", { scope: "participant", participant_uri: "agent:b" }, "human:a"));
  assert.equal(c.workspaces.get("w")!.members.get("agent:b")!.paused, false);
});
