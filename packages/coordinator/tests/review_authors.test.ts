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

test("the agent cannot add itself, or anyone, to its own required review, or approve it", () => {
  const { c, send, id } = requiredUnderReview();
  for (const to of [["agent:b"], ["human:c"]]) {
    refused(send("review.request", { task_id: id, artefact: DRAFT, to }),
            "The author of the work under review cannot request its review");
  }
  refused(send("decide.approve", { task_id: id }), "The author of the work under review cannot decide it");
  assert.deepEqual(task(c, id).review!.requested_to, ["human:a", "human:c"]);
  assert.equal(task(c, id).state, "review_requested");
});

/** The refusal of a review.request that would open a required review. */
const OPENS_ON_COMPLETE = "This task requires a person's review, which opens on task.complete; "
  + "review.request only adds reviewers to it once it is open";

test("before completing, nobody opens a required review with review.request", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b", review_required: true })).task_id;
  refused(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:a"] }), OPENS_ON_COMPLETE);
  refused(send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:b"] }, "human:a"), OPENS_ON_COMPLETE);
  // A person who is not an author cannot open it either, with an artefact
  // they supplied and themselves as its reviewer, and approve it.
  refused(send("review.request", { task_id: id, artefact: { by: "a" }, to: ["human:a"] }, "human:a"),
          OPENS_ON_COMPLETE);
  assert.equal(task(c, id).review, undefined);
  assert.equal(task(c, id).state, "created");
});

test("a completed required review cannot be reopened with review.request", () => {
  const { c, send, id } = requiredUnderReview();
  ok(send("decide.approve", { task_id: id }, "human:a"));
  assert.equal(task(c, id).state, "completed");
  refused(send("review.request", { task_id: id, artefact: { by: "c" }, to: ["human:c"] }, "human:c"),
          OPENS_ON_COMPLETE);
  assert.equal(task(c, id).state, "completed");
  assert.deepEqual(task(c, id).output, DRAFT);
});

test("another agent cannot be added to a required review", () => {
  const { send, id } = requiredUnderReview();
  refused(send("review.request", { task_id: id, artefact: DRAFT, to: ["agent:d"] }, "human:a"),
          "This task requires a person's review: agent:d is not a human member");
});

test("a required review names its reviewers: a broadcast scope is refused", () => {
  const { c, send, id } = requiredUnderReview();
  for (const scope of ["group:any", "workspace:w"]) {
    const r = send("review.request", { task_id: id, artefact: DRAFT, to: [scope] }, "human:a");
    assert.deepEqual(r.error, { code: E.PARAMS,
      message: `A review that requires a person names its reviewers, and ${scope} is an address scope` });
  }
  assert.deepEqual(task(c, id).review!.requested_to, ["human:a", "human:c"]);
  refused(send("decide.approve", { task_id: id }, "agent:d"),
          "This task requires a person's review: agent:d is not a human member");
  assert.equal(ok(send("decide.approve", { task_id: id }, "human:c")).state, "completed");
});

test("only the assignee completes a task that requires review", () => {
  // Completed by another member, the task would count that member as an
  // author, and routing it to a reviewer first would take that reviewer off
  // the review.
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "human:a", review_required: true },
                     "human:c")).task_id;
  refused(send("task.complete", { task_id: id, output: DRAFT }),
          "Only the assignee completes a task that requires review");
  assert.equal(task(c, id).state, "created");
  assert.equal(ok(send("task.complete", { task_id: id, output: DRAFT }, "human:a")).state, "review_requested");
  assert.deepEqual(task(c, id).review!.authors, ["human:a"]);
});

test("only the work's authors take it back from review, and an undecided review is not declined", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:a"] }));
  refused(send("task.update", { task_id: id, state: "in_progress" }, "agent:d"),
          "Only the authors of the work under review take it back from review");
  ok(send("task.update", { task_id: id, state: "in_progress" }));
  const declined = send("task.update", { task_id: id, state: "declined" });
  assert.deepEqual(declined.error, { code: E.PARAMS,
    message: "This task holds a review no decision has closed; a reviewer's decision ends it" });
  assert.equal(task(c, id).state, "in_progress");
  // Once a reviewer rejected the work, the review is decided, and its
  // author may give the task up.
  ok(send("task.complete", { task_id: id, output: { body: "v2" } }));
  ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));
  assert.equal(ok(send("task.update", { task_id: id, state: "declined" })).state, "declined");
});

test("an escalation of a task that held a review requires review", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:a"] }));
  const next = ok(send("escalate.raise", { original_task_id: id, new_task: { assignee: "agent:d" } })).new_task_id;
  assert.equal(task(c, next).review_required, true);
  assert.equal(ok(send("task.complete", { task_id: next, output: DRAFT }, "agent:d")).state, "review_requested");
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
  ok(send("task.complete", { task_id: required, output: DRAFT }));
  assert.deepEqual(task(c, required).review!.authors, ["agent:b"]);
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

test("a review restored without authors takes them from the task's history", () => {
  // A task one agent completed for another and then sent for review, and one
  // whose completion opened a review its assignee completed: the shapes a
  // store written before authors were recorded holds.
  const { c, send } = ready();
  const explicit = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("task.complete", { task_id: explicit, output: DRAFT }, "agent:d"));
  ok(send("review.request", { task_id: explicit, artefact: DRAFT, to: ["group:any"] }, "agent:d"));
  const implicit = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b",
                                            review_required: true })).task_id;
  ok(send("task.complete", { task_id: implicit, output: DRAFT }));
  const fresh = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: fresh, artefact: DRAFT, to: ["group:any"] }));
  // A review a person approved: the approval completed the task, and is not
  // a completion of the work.
  const approved = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("task.complete", { task_id: approved, output: DRAFT }, "agent:d"));
  ok(send("review.request", { task_id: approved, artefact: DRAFT, to: ["human:a"] }, "agent:d"));
  ok(send("decide.approve", { task_id: approved }, "human:a"));
  const snapshot = JSON.parse(JSON.stringify(c.snapshot()));
  for (const t of snapshot[0].tasks) delete t.review?.authors;
  const again = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0"] });
  again.restore(snapshot);
  const authors = (id: string) => again.workspaces.get("w")!.tasks.get(id)!.review!.authors;
  assert.deepEqual(authors(explicit), ["agent:b", "agent:d"]);
  assert.deepEqual(authors(implicit), ["agent:b"]);
  assert.deepEqual(authors(fresh), ["agent:b"]);
  assert.deepEqual(authors(approved), ["agent:b", "agent:d"]);
  const r = again.dispatch({ jsonrpc: "2.0", id: "x", method: "decide.approve",
                             params: { workspace: "w", from: "agent:d", task_id: explicit } });
  assert.equal(r.error?.code, E.NOT_AUTHORISED);
});

test("a member restored with a type its URI does not name takes the scheme's", () => {
  const { c, send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b",
                                      review_required: true }, "human:a")).task_id;
  ok(send("task.complete", { task_id: id, output: DRAFT }));
  // Written by a release that took the type a join declared.
  const snapshot = JSON.parse(JSON.stringify(c.snapshot()));
  const evil = { uri: "agent:evil", type: "human", role: "admin", joined: "t", keys: [], paused: false };
  snapshot[0].members.push(evil);
  snapshot[0].tasks.find((t: any) => t.id === id).review.requested_to.push("agent:evil");
  const again = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0"] });
  again.restore(snapshot);
  const member = again.workspaces.get("w")!.members.get("agent:evil")!;
  assert.equal(member.type, "agent");
  assert.equal(member.role, "participant");
  const r = again.dispatch({ jsonrpc: "2.0", id: "x", method: "decide.approve",
                             params: { workspace: "w", from: "agent:evil", task_id: id } }) as any;
  assert.equal(r.error?.message, "This task requires a person's review: agent:evil is not a human member");
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
    const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
    ok(send("task.complete", { task_id: id, output: DRAFT }));
    ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:a"] }));
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
