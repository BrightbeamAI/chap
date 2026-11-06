/**
 * review.acknowledge: a reviewer says they have begun the open review
 * (review/1.0 S3.6).
 *
 * The method was in the catalogue as spec-only, so a reviewer opening a
 * review left nothing on the log, and a reader could not tell a review
 * nobody had looked at from one in progress.
 *
 * Mirrors packages/coordinator-py/tests/test_review_acknowledge.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { E } from "../src/jsonrpc.js";
import { MemoryStore, type Store } from "../src/storage/store.js";

const DRAFT = { body: "draft" };
const MEMBERS: [string, string][] = [["human:a", "human"], ["human:c", "human"],
                                     ["agent:b", "agent"], ["agent:d", "agent"]];

function ready(store?: Store) {
  const c = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0"], deterministicIds: true,
                              deterministicClock: true, ...(store ? { store } : {}) });
  let n = 0;
  const send = (method: string, params: Record<string, unknown> = {}, actor = "agent:b") =>
    c.dispatch({ jsonrpc: "2.0", id: `c${++n}`, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create");
  for (const [uri, type] of MEMBERS) send("participant.join", { type }, uri);
  return { c, send, log: () => c.workspaces.get("w")!.audit.length };
}

const task = (c: Coordinator, id: string) => c.workspaces.get("w")!.tasks.get(id)!;
const ok = (r: any) => { assert.equal(r.error, undefined, JSON.stringify(r.error)); return r.result; };
const refusedWith = (r: any, code: number, message: string) => {
  assert.equal(r.error?.code, code, JSON.stringify(r));
  assert.equal(r.error?.message, message);
};

/** A task of agent:b's under an explicit review addressed to `to`. */
function underReview(to: string[] = ["human:a", "human:c"], required = false) {
  const r = ready();
  const id = ok(r.send("task.create", { kind: "k", input: {}, assignee: "agent:b",
                                        ...(required ? { review_required: true } : {}) })).task_id;
  if (required) ok(r.send("task.complete", { task_id: id, output: DRAFT }));
  else ok(r.send("review.request", { task_id: id, artefact: DRAFT, to }));
  return { ...r, id };
}

test("a reviewer's acknowledgement is recorded, names the round, and decides nothing", () => {
  const { c, send, log, id } = underReview();
  const before = log();
  const result = ok(send("review.acknowledge", { task_id: id }, "human:a"));
  const review = task(c, id).review!;
  assert.deepEqual(result, { acknowledged: true, requested_at: review.requested_at });
  assert.equal(log(), before + 1);
  assert.equal(c.workspaces.get("w")!.audit.at(-1)!.envelope!.method, "review.acknowledge");
  assert.equal(review.acknowledgements.length, 1);
  assert.equal(review.acknowledgements[0].reviewer, "human:a");
  assert.equal(typeof review.acknowledgements[0].ts, "string");
  assert.equal(task(c, id).state, "review_requested");
  assert.deepEqual(review.decisions, []);
});

test("a reviewer acknowledges a round once, and the refusal is not recorded", () => {
  const { c, send, log, id } = underReview();
  ok(send("review.acknowledge", { task_id: id }, "human:a"));
  const before = log();
  refusedWith(send("review.acknowledge", { task_id: id }, "human:a"), E.PARAMS, "Already acknowledged in this round");
  assert.equal(log(), before);
  ok(send("review.acknowledge", { task_id: id }, "human:c"));
  assert.deepEqual(task(c, id).review!.acknowledgements.map((a) => a.reviewer), ["human:a", "human:c"]);
});

test("a task with no open review cannot be acknowledged", () => {
  const { send } = ready();
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  refusedWith(send("review.acknowledge", { task_id: id }, "human:a"), E.NOT_REVIEWABLE,
              "Task not awaiting review: created");
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:a"] }));
  ok(send("decide.approve", { task_id: id }, "human:a"));
  refusedWith(send("review.acknowledge", { task_id: id }, "human:a"), E.NOT_REVIEWABLE,
              "Task not awaiting review: completed");
});

test("only someone who may decide the review may acknowledge it", () => {
  const { c, send, log, id } = underReview(["human:a"]);
  const before = log();
  refusedWith(send("review.acknowledge", { task_id: id }), E.NOT_AUTHORISED,
              "The author of the work under review cannot decide it");
  refusedWith(send("review.acknowledge", { task_id: id }, "human:c"), E.NOT_AUTHORISED,
              "Not an addressed reviewer for this task: human:c");
  refusedWith(send("review.acknowledge", { task_id: id }, "human:z"), E.NOT_AUTHORISED,
              "Not a workspace member: human:z");
  // The members' refusals are recorded; the outsider's is not (SPECIFICATION 10.1).
  assert.equal(log(), before + 2);
  assert.deepEqual(task(c, id).review!.acknowledgements, []);
});

test("an author cannot acknowledge a broadcast review of its own work", () => {
  const { send, id } = underReview(["workspace:w"]);
  refusedWith(send("review.acknowledge", { task_id: id }), E.NOT_AUTHORISED,
              "The author of the work under review cannot decide it");
  ok(send("review.acknowledge", { task_id: id }, "agent:d"));
});

test("a review that requires a person is acknowledged by a person", () => {
  const { send, id } = underReview([], true);
  refusedWith(send("review.acknowledge", { task_id: id }, "agent:d"), E.NOT_AUTHORISED,
              "This task requires a person's review: agent:d is not a human member");
  ok(send("review.acknowledge", { task_id: id }, "human:c"));
});

test("a new round starts with no acknowledgements, and an amendment keeps them", () => {
  const { c, send, id } = underReview(["human:a"]);
  const first = ok(send("review.acknowledge", { task_id: id }, "human:a")).requested_at;
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:c"] }, "agent:b"));
  assert.deepEqual(task(c, id).review!.acknowledgements.map((a) => a.reviewer), ["human:a"]);
  ok(send("decide.reject", { task_id: id, request_revision: true }, "human:a"));
  ok(send("task.complete", { task_id: id, output: { body: "second draft" } }));
  assert.deepEqual(task(c, id).review!.acknowledgements, []);
  const second = ok(send("review.acknowledge", { task_id: id }, "human:a")).requested_at;
  assert.notEqual(second, first);
  assert.equal(second, task(c, id).review!.requested_at);
});

test("acknowledgements are kept by the store, and a review stored without them has none", () => {
  const store = new MemoryStore();
  const { c, send } = ready(store);
  const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
  ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:a"] }));
  ok(send("review.acknowledge", { task_id: id }, "human:a"));
  const reopened = new Coordinator({ store });
  assert.deepEqual(task(reopened, id).review!.acknowledgements, task(c, id).review!.acknowledgements);

  const snapshot = JSON.parse(JSON.stringify(c.snapshot()));
  delete snapshot[0].tasks.find((t: any) => t.id === id).review.acknowledgements;
  const restored = new Coordinator({ defaultProfiles: ["core/1.0", "review/1.0"] });
  restored.restore(snapshot);
  assert.deepEqual(task(restored, id).review!.acknowledgements, []);
  const r = restored.dispatch({ jsonrpc: "2.0", id: "x", method: "review.acknowledge",
                                params: { workspace: "w", from: "human:a", task_id: id } }) as any;
  assert.equal(r.result?.acknowledged, true);
});

test("acknowledgements are written to a SQLite store", async () => {
  const { createRequire } = await import("node:module");
  const { mkdtempSync, rmSync } = await import("node:fs");
  const { join } = await import("node:path");
  const { tmpdir } = await import("node:os");
  const { SqliteStore } = await import("../src/storage/sqlite.js");
  try { createRequire(import.meta.url)("better-sqlite3"); } catch { return; }
  const dir = mkdtempSync(join(tmpdir(), "chap-acknowledge-"));
  try {
    const path = join(dir, "chap.db");
    const store = new SqliteStore(path);
    const { c, send } = ready(store);
    const id = ok(send("task.create", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
    ok(send("review.request", { task_id: id, artefact: DRAFT, to: ["human:a"] }));
    ok(send("review.acknowledge", { task_id: id }, "human:a"));
    store.close();
    const reopened = new SqliteStore(path);
    const again = new Coordinator({ store: reopened });
    assert.deepEqual(task(again, id).review!.acknowledgements, task(c, id).review!.acknowledgements);
    reopened.close();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("review.acknowledge belongs to review/1.0", () => {
  const c = new Coordinator({ defaultProfiles: ["core/1.0"] });
  c.dispatch({ jsonrpc: "2.0", id: "w", method: "workspace.create", params: { workspace: "w", from: "human:a" } });
  const r = c.dispatch({ jsonrpc: "2.0", id: "x", method: "review.acknowledge",
                         params: { workspace: "w", from: "human:a", task_id: "t" } }) as any;
  assert.equal(r.error?.code, E.METHOD);
  assert.equal(r.error?.data?.profile, "review/1.0");
});
