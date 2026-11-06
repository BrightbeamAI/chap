/**
 * The authors of the work under review cannot decide it, and a review that
 * requires a person is decided by one.
 *
 * On a task that required review the agent's decide.approve was refused, so
 * it called review.request with the same artefact and `to: [itself]`; the
 * coordinator widened the review, and the agent's next approval completed the
 * task (6 November review, item 1). A broadcast reviewer let any member
 * decide, the completer included. A task taken back from review with
 * task.update then completed with no decision at all when it did not require
 * review.
 *
 * Mirrors packages/coordinator-py/tests/test_review_authors.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { E } from "../src/jsonrpc.js";

const DRAFT = { body: "draft" };

function ready(members: [string, string][] = [["human:a", "human"], ["human:c", "human"],
                                              ["agent:b", "agent"], ["agent:d", "agent"]]) {
  const c = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0"], deterministicIds: true });
  let n = 0;
  const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:b") =>
    c.dispatch({ jsonrpc: "2.0", id: `c${++n}`, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create");
  for (const [uri, type] of members) send("participant.join", { type }, uri);
  return { c, send };
}

const task = (c: Coordinator, id: string) => c.workspaces.get("w")!.tasks.get(id)!;
const ok = (r: any) => { assert.equal(r.error, undefined, JSON.stringify(r.error)); return r.result; };
const refused = (r: any, message: string) => {
  assert.equal(r.error?.code, E.NOT_AUTHORISED, JSON.stringify(r));
  assert.equal(r.error?.message, message);
};

function requiredUnderReview() {
  const ready_ = ready();
  const id = ok(ready_.send("task.create", { kind: "k", input: {}, assignee: "agent:b",
                                             review_required: true })).task_id;
  assert.equal(ok(ready_.send("task.complete", { task_id: id, output: DRAFT })).state, "review_requested");
  return { ...ready_, id };
}

test("the agent cannot add itself to its own required review, or approve it", () => {
  const { c, send, id } = requiredUnderReview();
  refused(send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:b"] }),
          "The author of the work under review cannot review it: agent:b");
  refused(send("decide.approve", { task_id: id }), "The author of the work under review cannot decide it");
  assert.deepEqual(task(c, id).review!.requested_to, ["human:a", "human:c"]);
  assert.equal(task(c, id).state, "review_requested");
});

test("before completing, the agent cannot open a required review addressed to itself", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b", review_required: true })).task_id;
  refused(send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:b"] }),
          "The author of the work under review cannot review it: agent:b");
  assert.equal(task(c, id).review, undefined);
});

test("another agent cannot be added to a required review", () => {
  const { send, id } = requiredUnderReview();
  refused(send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:d"] }, "human:a"),
          "This task requires a person's review: agent:d is not a human member");
});

test("a broadcast reviewer on a required review still means a person", () => {
  const { c, send, id } = requiredUnderReview();
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["group:any"] }, "human:a"));
  refused(send("decide.approve", { task_id: id }, "agent:d"),
          "This task requires a person's review: agent:d is not a human member");
  refused(send("decide.approve", { task_id: id }), "The author of the work under review cannot decide it");
  assert.equal(ok(send("decide.approve", { task_id: id }, "human:c")).state, "completed");
  assert.equal(task(c, id).state, "completed");
});

test("the completer cannot decide a broadcast review of its own work", () => {
  // The bridges' pattern: complete, then request review of the output.
  const { send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("task.complete", { task_id: id, output: DRAFT }));
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["group:any"] }));
  for (const verb of ["decide.approve", "decide.reject"]) {
    refused(send(verb, { task_id: id }), "The author of the work under review cannot decide it");
  }
  refused(send("decide.override", { task_id: id, diff: [], rationale: "r" }),
          "The author of the work under review cannot decide it");
  refused(send("abstain.declare", { task_id: id, reason: "r" }),
          "The author of the work under review cannot decide it");
  assert.equal(ok(send("decide.approve", { task_id: id }, "agent:d")).state, "completed");
});

test("an explicit review of a task that does not require one keeps its own reviewers", () => {
  const { send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:d"] }));
  assert.equal(ok(send("decide.approve", { task_id: id }, "agent:d")).state, "completed");
});

test("a review records the assignee and the completer as its authors", () => {
  const { c, send } = ready();
  const required = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b",
                                            review_required: true })).task_id;
  ok(send("task.complete", { task_id: required, output: DRAFT }, "agent:d"));
  assert.deepEqual(task(c, required).review!.authors, ["agent:b", "agent:d"]);
  assert.deepEqual(task(c, required).review!.requested_to, ["human:a", "human:c"]);

  const explicit = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("task.complete", { task_id: explicit, output: DRAFT }, "agent:d"));
  ok(send("review.request", { task_id: explicit, artefact: DRAFT, to: ["human:a"] }));
  assert.deepEqual(task(c, explicit).review!.authors, ["agent:b", "agent:d"]);

  const fresh = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: fresh, artefact: DRAFT, to: ["human:a"] }));
  assert.deepEqual(task(c, fresh).review!.authors, ["agent:b"]);
});

test("a task that holds a review completes only through a decision", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:a"] }));
  ok(send("task.update", { task_id: id, state: "in_progress" }));
  const r = send("task.update", { task_id: id, state: "completed" });
  assert.equal(r.error?.code, E.PARAMS);
  assert.match(r.error.message, /holds a review/);
  assert.equal(ok(send("task.complete", { task_id: id, output: { body: "v2" } })).state, "review_requested");
  assert.deepEqual(task(c, id).review!.decisions, []);
  assert.deepEqual(task(c, id).review!.requested_to, ["human:a"]);
  assert.equal(ok(send("decide.approve", { task_id: id }, "human:a")).state, "completed");
  assert.deepEqual(task(c, id).output, { body: "v2" });
});

test("a completion that would leave no reviewer but its authors is refused", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:b"] }));
  ok(send("task.update", { task_id: id, state: "in_progress" }));
  refused(send("task.complete", { task_id: id, output: DRAFT }),
          "No reviewer of this task remains once the authors of the work are left out");
  assert.equal(task(c, id).state, "in_progress");
});

test("a new round of a required review leaves out a reviewer who is not a person", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b", review_required: true })).task_id;
  ok(send("task.complete", { task_id: id, output: DRAFT }));
  // A review written before this release could name an agent.
  task(c, id).review!.requested_to.push("agent:d");
  ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));
  ok(send("task.complete", { task_id: id, output: { body: "v2" } }));
  assert.deepEqual(task(c, id).review!.requested_to, ["human:a", "human:c"]);
});

test("a review restored without authors takes its assignee", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["group:any"] }));
  const snapshot = JSON.parse(JSON.stringify(c.snapshot()));
  delete snapshot[0].tasks.find((t: any) => t.id === id).review.authors;
  const again = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0"] });
  again.restore(snapshot);
  assert.deepEqual(again.workspaces.get("w")!.tasks.get(id)!.review!.authors, ["agent:b"]);
  const r = again.dispatch({ jsonrpc: "2.0", id: "x", method: "decide.approve",
                             params: { workspace: "w", from: "agent:b", task_id: id } });
  assert.equal(r.error?.code, E.NOT_AUTHORISED);
});

test("authors are written to the store, and a new round's authors replace them", async () => {
  const { createRequire } = await import("node:module");
  const { mkdtempSync, rmSync } = await import("node:fs");
  const { join } = await import("node:path");
  const { tmpdir } = await import("node:os");
  const { SqliteStore } = await import("../src/storage/sqlite.js");
  try { createRequire(import.meta.url)("better-sqlite3"); } catch { return; }
  const dir = mkdtempSync(join(tmpdir(), "chap-authors-"));
  try {
    const path = join(dir, "chap.db");
    const store = new SqliteStore(path);
    const c = new Coordinator({ store, defaultProfiles: ["core/1.0", "review/1.0"], deterministicIds: true });
    let n = 0;
    const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:b") =>
      c.dispatch({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from: actor, ...params } }) as any;
    send("workspace.create");
    for (const [uri, type] of [["human:a", "human"], ["agent:b", "agent"], ["agent:d", "agent"]]) {
      send("participant.join", { type }, uri);
    }
    const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b", review_required: true })).task_id;
    ok(send("task.complete", { task_id: id, output: DRAFT }));
    ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));
    ok(send("task.complete", { task_id: id, output: DRAFT }, "agent:d"));
    store.close();
    const reopened = new SqliteStore(path);
    const again = new Coordinator({ store: reopened });
    assert.deepEqual(again.workspaces.get("w")!.tasks.get(id)!.review!.authors, ["agent:b", "agent:d"]);
    assert.deepEqual(JSON.parse(JSON.stringify(again.snapshot())), JSON.parse(JSON.stringify(c.snapshot())));
    reopened.close();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
