/**
 * Authorisation tests: actor membership and reviewer-set eligibility.
 *
 * Pins the fix for the reported gap: decide.* (and the adjacent
 * actor-action methods) did not verify that `from` was a joined member,
 * so a decision could be attributed to a participant who never joined.
 * The coordinator now enforces:
 *
 *   - membership: `from` MUST be a joined workspace member; and
 *   - reviewer-set eligibility: to act on a review (decide.* / abstain),
 *     `from` MUST be one of the reviewers it was addressed to.
 *
 * SPECIFICATION.md S6.3 / S13.3 (`unknown_participant`), surfaced via
 * the NOT_AUTHORISED code.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/coordinator.js";
import { E } from "../src/jsonrpc.js";

function freshCoord(): Coordinator {
  return new Coordinator({
    deterministicIds: true,
    deterministicClock: true,
    defaultProfiles: ["core/1.0", "review/1.0", "routing/1.0"],
  });
}

function send(c: Coordinator, method: string, params: Record<string, unknown>) {
  return c.dispatch({ jsonrpc: "2.0", id: method, method, params });
}

/** Workspace with a task awaiting review by alice. Returns task id. */
function readyReview(c: Coordinator): string {
  send(c, "workspace.create", { workspace: "w", profiles: ["core/1.0", "review/1.0"] });
  send(c, "participant.join", { workspace: "w", from: "human:alice@x", type: "human" });
  send(c, "participant.join", { workspace: "w", from: "agent:bot@x", type: "agent" });
  const created = send(c, "task.create", {
    workspace: "w", from: "human:alice@x", kind: "draft",
    assignee: "agent:bot@x", input: { x: 1 },
  });
  const tid = (created.result as { task_id: string }).task_id;
  send(c, "task.complete", { workspace: "w", from: "agent:bot@x", task_id: tid, output: { draft: "hi" } });
  send(c, "review.request", {
    workspace: "w", from: "agent:bot@x", task_id: tid,
    to: ["human:alice@x"], artefact: { draft: "hi" },
  });
  return tid;
}

describe("membership floor", () => {
  test("decide.approve by a non-member is rejected", () => {
    const c = freshCoord();
    const tid = readyReview(c);
    const r = send(c, "decide.approve", { workspace: "w", from: "human:ghost@x", task_id: tid });
    assert.ok(r.error);
    assert.equal(r.error!.code, E.NOT_AUTHORISED);
  });

  test("decide.override by a non-member is rejected", () => {
    const c = freshCoord();
    const tid = readyReview(c);
    const r = send(c, "decide.override", {
      workspace: "w", from: "human:ghost@x", task_id: tid,
      diff: [{ op: "replace", path: "/draft", value: "x" }], rationale: "no",
    });
    assert.ok(r.error);
    assert.equal(r.error!.code, E.NOT_AUTHORISED);
  });

  test("task.complete by a non-member is rejected", () => {
    const c = freshCoord();
    send(c, "workspace.create", { workspace: "w", profiles: ["core/1.0", "review/1.0"] });
    send(c, "participant.join", { workspace: "w", from: "human:alice@x", type: "human" });
    send(c, "participant.join", { workspace: "w", from: "agent:bot@x", type: "agent" });
    const created = send(c, "task.create", {
      workspace: "w", from: "human:alice@x", kind: "draft", assignee: "agent:bot@x", input: {},
    });
    const tid = (created.result as { task_id: string }).task_id;
    const r = send(c, "task.complete", { workspace: "w", from: "agent:ghost@x", task_id: tid, output: {} });
    assert.ok(r.error);
    assert.equal(r.error!.code, E.NOT_AUTHORISED);
  });

  test("review.request by a non-member is rejected", () => {
    const c = freshCoord();
    send(c, "workspace.create", { workspace: "w", profiles: ["core/1.0", "review/1.0"] });
    send(c, "participant.join", { workspace: "w", from: "human:alice@x", type: "human" });
    send(c, "participant.join", { workspace: "w", from: "agent:bot@x", type: "agent" });
    const created = send(c, "task.create", {
      workspace: "w", from: "human:alice@x", kind: "draft", assignee: "agent:bot@x", input: {},
    });
    const tid = (created.result as { task_id: string }).task_id;
    send(c, "task.complete", { workspace: "w", from: "agent:bot@x", task_id: tid, output: {} });
    const r = send(c, "review.request", {
      workspace: "w", from: "agent:ghost@x", task_id: tid, to: ["human:alice@x"], artefact: {},
    });
    assert.ok(r.error);
    assert.equal(r.error!.code, E.NOT_AUTHORISED);
  });
});

describe("reviewer-set eligibility", () => {
  test("a member not in the reviewer set cannot decide", () => {
    const c = freshCoord();
    const tid = readyReview(c); // addressed to alice
    send(c, "participant.join", { workspace: "w", from: "human:bob@x", type: "human" });
    const r = send(c, "decide.approve", { workspace: "w", from: "human:bob@x", task_id: tid });
    assert.ok(r.error);
    assert.equal(r.error!.code, E.NOT_AUTHORISED);
  });

  test("a member not in the reviewer set cannot abstain", () => {
    const c = freshCoord();
    const tid = readyReview(c);
    send(c, "participant.join", { workspace: "w", from: "human:bob@x", type: "human" });
    const r = send(c, "abstain.declare", { workspace: "w", from: "human:bob@x", task_id: tid, reason: "no" });
    assert.ok(r.error);
    assert.equal(r.error!.code, E.NOT_AUTHORISED);
  });
});

describe("happy paths still work", () => {
  test("the addressed reviewer can approve", () => {
    const c = freshCoord();
    const tid = readyReview(c);
    const r = send(c, "decide.approve", { workspace: "w", from: "human:alice@x", task_id: tid });
    assert.equal(r.error, undefined);
    assert.equal((r.result as { state: string }).state, "completed");
  });

  test("the addressed reviewer can override", () => {
    const c = freshCoord();
    const tid = readyReview(c);
    const r = send(c, "decide.override", {
      workspace: "w", from: "human:alice@x", task_id: tid,
      diff: [{ op: "replace", path: "/draft", value: "edited" }],
      rationale: "warmer phrasing", tags: ["tone"],
    });
    assert.equal(r.error, undefined);
    assert.deepEqual((r.result as { applied: unknown }).applied, { draft: "edited" });
  });
});

describe("broadcast-scoped reviewer addressing", () => {
  // Task awaiting review, addressed to an arbitrary `to` value.
  function readyReviewTo(c: Coordinator, to: unknown): string {
    send(c, "workspace.create", { workspace: "w", profiles: ["core/1.0", "review/1.0"] });
    send(c, "participant.join", { workspace: "w", from: "human:alice@x", type: "human" });
    send(c, "participant.join", { workspace: "w", from: "agent:bot@x", type: "agent" });
    const created = send(c, "task.create", {
      workspace: "w", from: "human:alice@x", kind: "draft", assignee: "agent:bot@x", input: { x: 1 },
    });
    const tid = (created.result as { task_id: string }).task_id;
    send(c, "task.complete", { workspace: "w", from: "agent:bot@x", task_id: tid, output: { draft: "hi" } });
    send(c, "review.request", { workspace: "w", from: "agent:bot@x", task_id: tid, to, artefact: { draft: "hi" } });
    return tid;
  }

  test("a workspace-scoped review lets any member decide", () => {
    // Documented broadcast pattern (examples/03-review-and-approve.md):
    // the reviewer-set check must not reject a real member here.
    const c = freshCoord();
    const tid = readyReviewTo(c, ["workspace:w"]);
    const r = send(c, "decide.approve", { workspace: "w", from: "human:alice@x", task_id: tid });
    assert.equal(r.error, undefined);
    assert.equal((r.result as { state: string }).state, "completed");
  });

  test("a group-scoped review lets a member decide", () => {
    const c = freshCoord();
    const tid = readyReviewTo(c, ["group:reviewers"]);
    const r = send(c, "decide.approve", { workspace: "w", from: "human:alice@x", task_id: tid });
    assert.equal(r.error, undefined);
    assert.equal((r.result as { state: string }).state, "completed");
  });

  test("broadcast scope still rejects a non-member", () => {
    const c = freshCoord();
    const tid = readyReviewTo(c, ["workspace:w"]);
    const r = send(c, "decide.approve", { workspace: "w", from: "human:ghost@x", task_id: tid });
    assert.ok(r.error);
    assert.equal(r.error!.code, E.NOT_AUTHORISED);
  });
});
