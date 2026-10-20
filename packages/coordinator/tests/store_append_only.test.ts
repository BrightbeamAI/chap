/**
 * Each call writes its own audit entries and the objects it changed, so the
 * cost of a write does not grow with the log. Before 0.3.2 every call wrote
 * the whole workspace, and a long run slowed with the square of its length.
 *
 * The mirror of this file is
 * packages/coordinator-py/tests/test_store_append_only.py.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Coordinator } from "../src/coordinator.ts";
import { MemoryStore, StoreConflictError, type Store, type StoreChange, type WorkspaceRecord } from "../src/storage/store.ts";

const PROFILES = ["core/1.0", "review/1.0", "control/1.0", "handoff/1.0",
  "whisper/1.0", "deliberation/1.0", "routing/1.0"];
const OPTIONS = { deterministicIds: true, deterministicClock: true, enableChain: true, defaultProfiles: PROFILES };

class Recording extends MemoryStore {
  changes: StoreChange[] = [];
  override apply(change: StoreChange): void {
    this.changes.push(JSON.parse(JSON.stringify(change)));
    super.apply(change);
  }
}

class Flaky extends MemoryStore {
  failures = 0;
  override apply(change: StoreChange): void {
    if (this.failures > 0) {
      this.failures--;
      throw new Error("disk full");
    }
    super.apply(change);
  }
}

type Send = (method: string, from: string, params?: Record<string, unknown>) => any;

function driver(c: Coordinator, workspace = "w"): Send {
  let n = 0;
  return (method, from, params = {}) => c.dispatch({
    jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace, from, ...params },
  } as never);
}

function ok(reply: any): any {
  assert.ok("result" in reply, JSON.stringify(reply.error));
  return reply.result;
}

/** A workspace that has used every collection a store keeps. */
function exercise(c: Coordinator): void {
  const send = driver(c);
  ok(send("workspace.create", "human:a", { profiles: PROFILES }));
  for (const [who, type] of [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"], ["agent:d", "agent"]]) {
    ok(send("participant.join", who, { type }));
  }
  const first = ok(send("task.create", "human:a",
    { kind: "draft", input: { n: 1 }, assignee: "agent:b", idempotency_key: "k-1" })).task_id;
  ok(send("task.update", "agent:b", { task_id: first, state: "in_progress" }));
  ok(send("review.request", "agent:b", { task_id: first, artefact: { text: "draft" }, to: ["human:a"], rule: "any_one_approves" }));
  ok(send("decide.override", "human:a", { task_id: first, rationale: "tone",
    diff: [{ op: "replace", path: "/text", value: "final" }], tags: ["tone"] }));
  const second = ok(send("task.create", "human:a", { kind: "draft", input: { n: 2 }, assignee: "agent:d" })).task_id;
  ok(send("review.request", "agent:d", { task_id: second, artefact: { text: "x" }, to: ["human:c"] }));
  ok(send("decide.reject", "human:c", { task_id: second, comment: "no", request_revision: true }));
  const third = ok(send("task.create", "human:a", { kind: "plan", input: {}, assignee: "agent:b" })).task_id;
  const late = ok(send("whisper.ask", "agent:b", { task_id: third, to: ["human:a"], question: "now?",
    deadline_ms: 0, default_if_lapsed: "no" })).whisper_id;
  const asked = ok(send("whisper.ask", "agent:b", { task_id: third, to: ["human:c"], question: "later?",
    deadline_ms: 600_000, default_if_lapsed: "no" })).whisper_id;
  ok(send("whisper.answer", "human:c", { whisper_id: asked, answer: "yes" }));
  const lapsed = c.checkWhisperLapses!("w", "2100-01-01T00:00:00.000Z");
  assert.equal(lapsed.length, 1);
  assert.equal(c.workspaces.get("w")!.whispers.get(late)!.state, "lapsed");
  const delib = ok(send("deliberate.open", "human:a", { to: ["human:a", "human:c"], rule: "any_one_approves",
    question: "ship?", task_id: third })).deliberation_id;
  ok(send("deliberate.vote", "human:c", { deliberation_id: delib, vote: "yea" }));
  ok(send("deliberate.close", "human:a", { deliberation_id: delib }));
  const handoff = ok(send("handoff.propose", "agent:b", { to: "human:c", tasks: [{ task_id: third }] })).handoff_id;
  ok(send("handoff.accept", "human:c", { handoff_id: handoff }));
  const fourth = ok(send("task.create", "human:a", { kind: "route", input: {}, assignee: "agent:d" })).task_id;
  ok(send("task.route", "human:a", { task_id: fourth, candidates: ["agent:b", "agent:d"] }));
  const snap = ok(send("control.snapshot", "human:a", {})).snapshot_artefact_id;
  ok(send("control.set_mode_ceiling", "human:a", { new_ceiling: "trial", reason: "hold" }));
  ok(send("control.rollback", "human:a", { to_snapshot_artefact_id: snap }));
  ok(send("control.pause", "human:a", { task_id: fourth, reason: "hold" }));
  ok(send("control.resume", "human:a", { task_id: fourth }));
  ok(send("participant.leave", "agent:d"));
  const ws = c.workspaces.get("w")!;
  for (const name of ["members", "tasks", "overrides", "whispers", "deliberations",
    "handoffs", "snapshots", "route_decisions"] as const) {
    assert.ok(ws[name].size > 0, `${name} is empty`);
  }
  assert.deepEqual(Object.keys(ws.idempotency_keys), ["k-1"]);
}

function plain(c: Coordinator): unknown {
  return JSON.parse(JSON.stringify(c.snapshot()));
}

describe("writes", () => {
  test("each write carries the call's entries and the objects it touched, at a flat size", () => {
    const store = new Recording();
    const c = new Coordinator({ ...OPTIONS, store });
    const send = driver(c);
    ok(send("workspace.create", "human:a"));
    ok(send("participant.join", "human:a", { type: "human" }));
    ok(send("participant.join", "agent:b", { type: "agent" }));
    let version = store.changes.at(-1)!.version;
    const sizes: number[] = [];
    for (let k = 0; k < 200; k++) {
      store.changes.length = 0;
      const id = ok(send("task.create", "human:a", { kind: "k", input: { k }, assignee: "agent:b" })).task_id;
      ok(send("task.complete", "agent:b", { task_id: id, output: { k } }));
      assert.equal(store.changes.length, 2);
      for (const change of store.changes) {
        assert.equal(change.base, version);
        assert.equal(change.entries.length, 1);
        assert.equal(change.replace, undefined);
        assert.ok(Object.keys(change.objects.tasks ?? {}).length <= 1);
        version = change.version;
      }
      sizes.push(store.changes.reduce((n, ch) => n + JSON.stringify(ch).length, 0));
    }
    // The two-hundredth task is written in the same space as the hundredth,
    // whose numbers have as many digits.
    assert.equal(sizes[199], sizes[100]);
  });

  test("a restart brings back every collection as it was", () => {
    const store = new MemoryStore();
    const c = new Coordinator({ ...OPTIONS, store });
    exercise(c);
    const restarted = new Coordinator({ ...OPTIONS, store });
    assert.deepEqual(plain(restarted), plain(c));
    const verify = restarted.dispatch({ jsonrpc: "2.0", id: "v", method: "audit.verify_chain",
      params: { workspace: "w", from: "human:a" } } as never) as any;
    assert.equal(verify.result.ok, true);
  });

  test("a lapsed whisper is written, though the lapse check runs outside a call", () => {
    const store = new MemoryStore();
    const c = new Coordinator({ ...OPTIONS, store });
    const send = driver(c);
    ok(send("workspace.create", "human:a", { profiles: PROFILES }));
    ok(send("participant.join", "human:a", { type: "human" }));
    ok(send("participant.join", "agent:b", { type: "agent" }));
    const task = ok(send("task.create", "human:a", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
    const id = ok(send("whisper.ask", "agent:b", { task_id: task, to: ["human:a"], question: "q",
      deadline_ms: 0, default_if_lapsed: "no" })).whisper_id;
    c.checkWhisperLapses!("w", "2100-01-01T00:00:00.000Z");
    const restarted = new Coordinator({ ...OPTIONS, store }).workspaces.get("w")!;
    assert.equal(restarted.whispers.get(id)!.state, "lapsed");
    assert.equal(restarted.audit.length, c.workspaces.get("w")!.audit.length);
  });

  test("a workspace restored from a snapshot is written whole on its next entry", () => {
    const source = new Coordinator(OPTIONS);
    exercise(source);
    const store = new MemoryStore();
    const c = new Coordinator({ ...OPTIONS, store });
    c.restore(JSON.parse(JSON.stringify(source.snapshot())));
    ok(driver(c)("participant.join", "human:e", { type: "human" }));
    assert.deepEqual(plain(new Coordinator({ ...OPTIONS, store })), plain(c));
  });
});

describe("objects the caller holds", () => {
  test("are not shared with the workspace, in what a call sends or returns", () => {
    const store = new MemoryStore();
    const c = new Coordinator({ ...OPTIONS, store });
    const send = driver(c);
    ok(send("workspace.create", "human:a", { profiles: PROFILES }));
    for (const [who, type] of [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"]]) {
      ok(send("participant.join", who, { type }));
    }
    const t1 = ok(send("task.create", "human:a", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
    const t2 = ok(send("task.create", "human:a", { kind: "k", input: {}, assignee: "agent:b" })).task_id;
    const reviewers = ["human:a"];
    ok(send("review.request", "agent:b", { task_id: t1, artefact: { x: 1 }, to: reviewers }));
    ok(send("review.request", "agent:b", { task_id: t2, artefact: { x: 2 }, to: reviewers }));
    // Widening the second review leaves the first as it was.
    ok(send("review.request", "agent:b", { task_id: t2, artefact: { x: 2 }, to: ["human:c"] }));
    const ws = c.workspaces.get("w")!;
    assert.deepEqual(ws.tasks.get(t1)!.review!.requested_to, ["human:a"]);
    assert.deepEqual(reviewers, ["human:a"]);
    // Changing a result changes nothing in the workspace.
    const described = ok(send("workspace.describe", "human:a"));
    described.profiles.push("not/1.0");
    assert.ok(!ws.profiles.includes("not/1.0"));
    assert.deepEqual(plain(new Coordinator({ ...OPTIONS, store })), plain(c));
  });

  test("a map an application replaced is tracked again", () => {
    const store = new MemoryStore();
    const errors: unknown[] = [];
    const c = new Coordinator({ ...OPTIONS, store, onStoreError: (e) => { errors.push(e); } });
    const send = driver(c);
    ok(send("workspace.create", "human:a"));
    ok(send("participant.join", "human:a", { type: "human" }));
    const ws = c.workspaces.get("w")!;
    ws.tasks = new Map(ws.tasks);
    for (let i = 0; i < 3; i++) ok(send("task.create", "human:a", { kind: "k", input: {}, assignee: "human:a" }));
    assert.deepEqual(errors, []);
    assert.deepEqual(plain(new Coordinator({ ...OPTIONS, store })), plain(c));
  });
});

describe("failed writes", () => {
  test("the next write carries what a failed one did not", () => {
    const store = new Flaky();
    const seen: Array<{ id: string; version: number }> = [];
    const c = new Coordinator({ ...OPTIONS, store, onStoreError: (_e, r) => { seen.push(r); } });
    const send = driver(c);
    ok(send("workspace.create", "human:a"));
    ok(send("participant.join", "human:a", { type: "human" }));
    store.failures = 2;
    ok(send("participant.join", "agent:b", { type: "agent" }));
    ok(send("task.create", "human:a", { kind: "k", input: {}, assignee: "agent:b" }));
    assert.equal(seen.length, 2);
    ok(send("participant.join", "human:c", { type: "human" }));
    assert.deepEqual(plain(new Coordinator({ ...OPTIONS, store })), plain(c));
  });

  test("with no onStoreError a failed write is written to stderr", () => {
    const store = new Flaky();
    const c = new Coordinator({ ...OPTIONS, store });
    const send = driver(c);
    ok(send("workspace.create", "human:a"));
    const lines: string[] = [];
    const original = console.error;
    console.error = (...args: unknown[]) => { lines.push(args.join(" ")); };
    try {
      store.failures = 1;
      ok(send("participant.join", "human:a", { type: "human" }));
    } finally {
      console.error = original;
    }
    assert.equal(lines.length, 1);
    assert.match(lines[0], /workspace w .*disk full/);
  });

  test("a store with only save is told when the workspace cannot be serialised", () => {
    const saved: WorkspaceRecord[] = [];
    const store: Store = { load: () => [], save: (r) => { saved.push(r); }, delete: () => {} };
    const seen: Array<{ id: string; version: number }> = [];
    const c = new Coordinator({ ...OPTIONS, store, onStoreError: (_e, r) => { seen.push(r); } });
    const send = driver(c);
    ok(send("workspace.create", "human:a"));
    const internals = c as unknown as { snapshotOf: (ws: unknown) => unknown };
    internals.snapshotOf = () => { throw new Error("cannot serialise"); };
    try {
      ok(send("participant.join", "human:a", { type: "human" }));
    } finally {
      delete (internals as { snapshotOf?: unknown }).snapshotOf;
    }
    assert.ok(seen.length > 0);
    assert.ok(seen.every(r => r.id === "w"));
    ok(send("participant.join", "agent:b", { type: "agent" }));
    const last = saved.at(-1)!;
    const restored = new Coordinator({ ...OPTIONS, store: { load: () => [last], save: () => {}, delete: () => {} } });
    assert.deepEqual(plain(restored), plain(c));
  });
});

describe("a second writer", () => {
  test("is refused and reported, and nothing stored is lost", () => {
    const store = new MemoryStore();
    const errors: Array<[unknown, { id: string; version: number }]> = [];
    const make = () => new Coordinator({ ...OPTIONS, store, onStoreError: (e, r) => { errors.push([e, r]); } });
    const a = make();
    ok(driver(a)("workspace.create", "human:alice"));
    ok(driver(a)("participant.join", "human:alice", { type: "human" }));
    const b = make();
    ok(driver(b)("participant.join", "human:bob", { type: "human" }));
    assert.equal(errors.length, 0);
    // A still holds its older state and writes again.
    ok(driver(a)("participant.join", "human:carol", { type: "human" }));
    assert.equal(errors.length, 1);
    const [err, record] = errors[0];
    assert.ok(err instanceof StoreConflictError);
    assert.deepEqual([err.held, err.base], [3, 2]);
    assert.deepEqual(record, { id: "w", version: 3 });
    const survivor = make().workspaces.get("w")!;
    assert.ok(survivor.members.has("human:bob"));
    assert.ok(!survivor.members.has("human:carol"));
  });
});

// ---- SqliteStore -------------------------------------------------------

const require_ = createRequire(import.meta.url);
let sqliteAvailable = true;
let sqliteError = "";
try {
  const Probe = require_("better-sqlite3");
  new Probe(":memory:").close();
} catch (e) {
  sqliteAvailable = false;
  sqliteError = e instanceof Error ? e.message.split("\n")[0] : String(e);
}
if (!sqliteAvailable && process.env.CHAP_REQUIRE_SQLITE) {
  throw new Error(`better-sqlite3 is required when CHAP_REQUIRE_SQLITE is set: ${sqliteError}`);
}

describe("SqliteStore", { skip: !sqliteAvailable }, () => {
  const tmp = mkdtempSync(join(tmpdir(), "chap-append-"));

  test("holds each entry once and restarts to the same state", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.ts");
    const path = join(tmp, "rt.db");
    const store = new SqliteStore(path);
    const c = new Coordinator({ ...OPTIONS, store });
    exercise(c);
    const count = (store.db.prepare("SELECT COUNT(*) AS n FROM chap_entries").get() as { n: number }).n;
    assert.equal(count, c.workspaces.get("w")!.audit.length);
    store.close();
    const reopened = new SqliteStore(path);
    assert.deepEqual(plain(new Coordinator({ ...OPTIONS, store: reopened })), plain(c));
    reopened.close();
  });

  test("a view in the old table's place reads the current state", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.ts");
    const store = new SqliteStore(join(tmp, "view.db"));
    const c = new Coordinator({ ...OPTIONS, store });
    exercise(c);
    const row = store.db.prepare("SELECT id, data, version FROM chap_workspaces").get() as
      { id: string; data: string; version: number };
    const data = JSON.parse(row.data);
    const ws = c.workspaces.get("w")!;
    assert.equal(row.id, "w");
    assert.equal(row.version, ws.audit.length);
    assert.equal(data.audit.length, ws.audit.length);
    assert.deepEqual(data.audit.map((e: { seq: number }) => e.seq), ws.audit.map(e => e.seq));
    assert.deepEqual(Object.keys(data.tasks).sort(), [...ws.tasks.keys()].sort());
    assert.equal(data.chain_head, ws.chain_head);
    store.close();
  });

  test("a file from an earlier release is moved into the new tables", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.ts");
    const Database = require_("better-sqlite3");
    const source = new Coordinator(OPTIONS);
    exercise(source);
    const snapshot = (source.snapshot() as Array<Record<string, unknown>>)[0];
    // The Python coordinator wrote its collections keyed by id.
    const keyed: Record<string, unknown> = { ...snapshot, id: "w-py" };
    for (const name of ["members", "tasks", "overrides", "whispers", "deliberations", "handoffs", "snapshots", "route_decisions"]) {
      const key = name === "members" ? "uri" : "id";
      keyed[name] = Object.fromEntries((snapshot[name] as Array<Record<string, string>>).map(x => [x[key], x]));
    }
    const path = join(tmp, "old.db");
    const old = new Database(path);
    old.exec(`CREATE TABLE chap_workspaces (id TEXT PRIMARY KEY, data TEXT NOT NULL,
      version INTEGER NOT NULL, updated_at TEXT NOT NULL);
      CREATE INDEX idx_chap_workspaces_updated_at ON chap_workspaces (updated_at);`);
    const put = old.prepare("INSERT INTO chap_workspaces VALUES (?, ?, ?, ?)");
    put.run("w", JSON.stringify(snapshot), 7, "t1");
    put.run("w-py", JSON.stringify(keyed), 7, "t1");
    put.run("w-bad", "{bad json", 1, "t1");
    old.close();

    const warned: string[] = [];
    const original = console.warn;
    console.warn = (...args: unknown[]) => { warned.push(args.join(" ")); };
    let store;
    try {
      store = new SqliteStore(path);
    } finally {
      console.warn = original;
    }
    assert.equal(warned.length, 1);
    assert.match(warned[0], /w-bad .*chap_workspaces_v1/);
    const c = new Coordinator({ ...OPTIONS, store });
    assert.deepEqual([...c.workspaces.keys()].sort(), ["w", "w-py"]);
    assert.deepEqual((plain(c) as unknown[]).find((w: any) => w.id === "w"), JSON.parse(JSON.stringify(snapshot)));
    assert.equal(c.workspaces.get("w-py")!.audit.length, (snapshot.audit as unknown[]).length);
    // The old rows are kept, unchanged, and the old index is gone.
    const kept = store.db.prepare("SELECT id, data FROM chap_workspaces_v1 ORDER BY id").all() as Array<{ id: string; data: string }>;
    assert.deepEqual(kept.map(r => r.id), ["w", "w-bad", "w-py"]);
    assert.equal(kept[1].data, "{bad json");
    assert.equal(store.db.prepare("SELECT 1 FROM sqlite_master WHERE name = 'idx_chap_workspaces_updated_at'").get(), undefined);
    // It keeps working, and opening it again moves nothing twice.
    ok(driver(c)("participant.join", "human:e", { type: "human" }));
    store.close();
    const again = new SqliteStore(path);
    const restarted = new Coordinator({ ...OPTIONS, store: again });
    assert.deepEqual(plain(restarted), plain(c));
    again.close();
  });

  test("an application's own view of the old table reads the current state after the upgrade", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.ts");
    const Database = require_("better-sqlite3");
    const path = join(tmp, "userview.db");
    const old = new Database(path);
    old.exec(`CREATE TABLE chap_workspaces (id TEXT PRIMARY KEY, data TEXT NOT NULL,
      version INTEGER NOT NULL, updated_at TEXT NOT NULL);
      CREATE VIEW my_report AS SELECT id, json_array_length(data, '$.audit') AS n FROM chap_workspaces;`);
    old.prepare("INSERT INTO chap_workspaces VALUES ('w', ?, 1, 't')").run(JSON.stringify(
      { id: "w", created: "t", state: "active", profiles: ["core/1.0"], members: [], tasks: [], audit: [] }));
    old.close();
    const store = new SqliteStore(path);
    ok(driver(new Coordinator({ store }))("participant.join", "agent:a", { type: "agent" }));
    assert.deepEqual(store.db.prepare("SELECT id, n FROM my_report").get(), { id: "w", n: 1 });
    store.close();
  });

  test("two coordinators on one file: the second writer is refused", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.ts");
    const path = join(tmp, "two.db");
    const errors: unknown[] = [];
    const make = () => new Coordinator({ ...OPTIONS, store: new SqliteStore(path), onStoreError: (e) => { errors.push(e); } });
    const a = make();
    ok(driver(a)("workspace.create", "human:alice"));
    const b = make();
    ok(driver(b)("participant.join", "human:bob", { type: "human" }));
    ok(driver(a)("participant.join", "human:carol", { type: "human" }));
    assert.equal(errors.length, 1);
    assert.ok(errors[0] instanceof StoreConflictError);
    const survivor = make().workspaces.get("w")!;
    assert.ok(survivor.members.has("human:bob"));
    assert.ok(!survivor.members.has("human:carol"));
  });

  test("cleanup", () => {
    rmSync(tmp, { recursive: true, force: true });
  });
});
