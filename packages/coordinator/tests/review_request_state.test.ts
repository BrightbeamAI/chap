/**
 * Regression: review.request only opens a review on work that has not stopped.
 *
 * task.complete refuses a task that has been stopped so that a completion "can
 * neither revive a terminated task nor bypass a pause". review.request carried
 * no such check, so a request revived a cancelled or superseded task and pulled
 * a paused one back into play.
 *
 * Refused from: cancelled, superseded, paused, with `-32010`. `completed`
 * stays legal: completing and then requesting review is how the framework
 * bridges submit a draft.
 *
 * Mirrors packages/coordinator-py/tests/test_review_request_state.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { E } from "../src/jsonrpc.js";

const PROFILES = ["core/1.0", "review/1.0", "control/1.0"];
const ARTEFACT = { text: "draft" };

function ready() {
  const c = new Coordinator({ defaultProfiles: PROFILES, deterministicIds: true });
  const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:b") =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create", { profiles: PROFILES });
  for (const [uri, type] of [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"]]) {
    send("participant.join", { type }, uri);
  }
  return { c, send };
}

function taskIn(send: any, state: string): string {
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  switch (state) {
    case "created":
      break;
    case "in_progress":
      send("task.update", { task_id: id, state: "in_progress" });
      break;
    case "review_requested":
      send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });
      break;
    case "completed":
      send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });
      send("decide.approve", { task_id: id, comment: "ok", rationale: "ok" }, "human:a");
      break;
    case "declined":
      send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });
      send("decide.reject", { task_id: id, comment: "no", rationale: "no" }, "human:a");
      break;
    case "abstained":
      send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });
      send("abstain.declare", { task_id: id, reason: "conflict of interest" }, "human:a");
      break;
    case "escalated":
      send("escalate.raise", { original_task_id: id, reason: "above me",
                               new_task: { kind: "k", input: {}, assignee: "agent:b" } }, "human:a");
      break;
    case "paused":
      send("control.pause", { task_id: id, reason: "hold" }, "human:a");
      break;
    case "cancelled":
      send("control.cancel", { task_id: id, reason: "not needed" }, "human:a");
      break;
    case "superseded":
      send("control.supersede", { task_id: id, reason: "redone",
                                  successor_task: { kind: "k", input: {}, assignee: "agent:b" } }, "human:a");
      break;
    default:
      throw new Error(`no recipe for ${state}`);
  }
  return id;
}

const task = (c: any, id: string) => c.workspaces.get("w").tasks.get(id);
const stateOf = (c: any, id: string) => task(c, id).state;

for (const state of ["created", "in_progress", "completed", "declined", "abstained", "escalated"]) {
  test(`review.request still opens on a ${state} task`, () => {
    const { c, send } = ready();
    const id = taskIn(send, state);
    const r = send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });
    assert.equal(r.error, undefined, JSON.stringify(r.error));
    assert.equal(stateOf(c, id), "review_requested");
  });
}

test("the framework bridge pattern still works", () => {
  // Every published bridge does task.complete then review.request. If this
  // fails, five packages are broken.
  const { c, send } = ready();
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  send("task.complete", { task_id: id, output: ARTEFACT });
  assert.equal(stateOf(c, id), "completed");
  const r = send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });
  assert.equal(r.error, undefined, JSON.stringify(r.error));
  assert.equal(stateOf(c, id), "review_requested");
});

test("an open review can still be widened", () => {
  const { c, send } = ready();
  const id = taskIn(send, "review_requested");
  const r = send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:c" });
  assert.equal(r.result.amended, true);
  assert.deepEqual([...task(c, id).review.requested_to].sort(), ["human:a", "human:c"]);
});

for (const state of ["cancelled", "superseded", "paused"]) {
  test(`a ${state} task cannot be pulled back into review`, () => {
    const { c, send } = ready();
    const id = taskIn(send, state);
    assert.equal(stateOf(c, id), state, "the fixture did not reach the state under test");

    const r = send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });

    assert.notEqual(r.error, undefined, `review.request re-opened a ${state} task`);
    assert.equal(r.error.code, E.NOT_REVIEWABLE);
    assert.ok(r.error.message.includes(state), r.error.message);
    assert.equal(stateOf(c, id), state, "the refused request still moved the task");
  });

  test(`the refusal on a ${state} task records nothing`, () => {
    // A refused call must not append. A stopped task that "grew" an entry would
    // leave an audit chain describing a review that never opened.
    const { send } = ready();
    const id = taskIn(send, state);
    const before = send("audit.read", {}, "human:a").result.entries.length;
    send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });
    const after = send("audit.read", {}, "human:a").result.entries.length;
    assert.equal(after, before);
  });
}

test("a paused task resumes before it can be reviewed", () => {
  // The pause is not a dead end; it just has to be lifted deliberately.
  const { c, send } = ready();
  const id = taskIn(send, "paused");
  assert.notEqual(send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" }).error,
                  undefined);
  send("control.resume", { task_id: id, reason: "carry on" }, "human:a");
  assert.equal(stateOf(c, id), "in_progress");
  const r = send("review.request", { task_id: id, artefact: ARTEFACT, to: "human:a" });
  assert.equal(r.error, undefined, JSON.stringify(r.error));
  assert.equal(stateOf(c, id), "review_requested");
});
