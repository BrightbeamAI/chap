/**
 * Persistence tests for the Store interface.
 *
 * Covers:
 *   - MemoryStore default (no persistence between coordinator instances)
 *   - SqliteStore round-trip: workspaces, tasks, audit, chain head
 *   - In-memory SQLite (`:memory:`) for tests that need isolation
 *
 * Skips the SqliteStore tests when better-sqlite3 is not installed,
 * so the suite stays green on minimal CI environments.
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { Coordinator } from "../src/coordinator.js";
import { MemoryStore } from "../src/storage/store.js";

function makeEnvelope(method: string, params: Record<string, unknown>, id = "1"): any {
  return { jsonrpc: "2.0", id, method, params };
}

describe("MemoryStore", () => {
  test("is the default when no store is passed", () => {
    const c = new Coordinator();
    assert.ok(c.store instanceof MemoryStore);
  });

  test("starts empty and accepts saves", () => {
    const store = new MemoryStore();
    assert.equal(store.load().length, 0);
    store.save({ id: "wsp_test", data: { id: "wsp_test" }, version: 1, updated_at: "t" });
    const loaded = store.load();
    assert.equal(loaded.length, 1);
    assert.equal(loaded[0].id, "wsp_test");
  });

  test("does not persist across coordinator instances", () => {
    const c1 = new Coordinator();
    c1.dispatch(makeEnvelope("workspace.create", { workspace: "wsp_a" }));

    const c2 = new Coordinator();
    assert.equal(c2.workspaces.size, 0,
      "MemoryStore is per-instance; a fresh coordinator has nothing");
  });
});

describe("Coordinator persistence", () => {
  test("persists workspace creation through MemoryStore (sanity)", () => {
    const store = new MemoryStore();
    const c = new Coordinator({ store });
    c.dispatch(makeEnvelope("workspace.create", { workspace: "wsp_x" }));
    const records = store.load();
    assert.equal(records.length, 1);
    assert.equal(records[0].id, "wsp_x");
    assert.ok(records[0].version >= 1);
  });

  test("rehydrates from store on construction", () => {
    const store = new MemoryStore();
    const c1 = new Coordinator({ store });
    c1.dispatch(makeEnvelope("workspace.create", { workspace: "wsp_y" }));
    c1.dispatch(makeEnvelope("participant.join", {
      workspace: "wsp_y", from: "human:alice@example.org", type: "human",
    }, "2"));

    // Same store, fresh coordinator. The new instance should see the workspace.
    const c2 = new Coordinator({ store });
    assert.equal(c2.workspaces.size, 1);
    const ws = c2.workspaces.get("wsp_y");
    assert.ok(ws, "workspace rehydrated");
    assert.ok(ws.members.has("human:alice@example.org"), "member rehydrated");
  });

  test("audit chain head survives a restart", () => {
    const store = new MemoryStore();
    const c1 = new Coordinator({ store, enableChain: true });
    c1.dispatch(makeEnvelope("workspace.create", { workspace: "wsp_z" }));
    c1.dispatch(makeEnvelope("participant.join", {
      workspace: "wsp_z", from: "human:bob@example.org", type: "human",
    }, "2"));
    const head1 = c1.workspaces.get("wsp_z")!.chain_head;
    assert.ok(head1);

    const c2 = new Coordinator({ store, enableChain: true });
    const head2 = c2.workspaces.get("wsp_z")!.chain_head;
    assert.equal(head2, head1, "chain head identical after rehydration");
  });
});

// ---- SqliteStore (skipped if better-sqlite3 unavailable) -----------

let sqliteAvailable = true;
try {
  require("better-sqlite3");
} catch {
  sqliteAvailable = false;
}

describe("SqliteStore", { skip: !sqliteAvailable }, () => {
  const tmp = mkdtempSync(join(tmpdir(), "chap-sqlite-"));

  test("round-trips workspace + tasks to disk", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.js");
    const dbPath = join(tmp, "rt.db");

    const c1 = new Coordinator({ store: new SqliteStore(dbPath) });
    c1.dispatch(makeEnvelope("workspace.create",
      { workspace: "wsp_disk", profiles: ["core/1.0", "review/1.0"] }));
    c1.dispatch(makeEnvelope("participant.join",
      { workspace: "wsp_disk", from: "human:me@local", type: "human" }, "2"));
    c1.dispatch(makeEnvelope("participant.join",
      { workspace: "wsp_disk", from: "agent:bot#v1", type: "agent" }, "3"));
    c1.dispatch(makeEnvelope("task.create", {
      workspace: "wsp_disk",
      from: "human:me@local",
      assignee: "agent:bot#v1",
      kind: "draft", input: { x: 1 },
    }, "4"));
    (c1.store as any).close?.();

    // Fresh coordinator pointed at the same file: full state should rehydrate.
    const c2 = new Coordinator({ store: new SqliteStore(dbPath) });
    const ws = c2.workspaces.get("wsp_disk");
    assert.ok(ws);
    assert.equal(ws.members.size, 2);
    assert.equal(ws.tasks.size, 1);
    assert.equal(ws.audit.length, 4);
    (c2.store as any).close?.();
  });

  test("in-memory SQLite isolates tests", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.js");
    const c1 = new Coordinator({ store: new SqliteStore(":memory:") });
    const c2 = new Coordinator({ store: new SqliteStore(":memory:") });
    c1.dispatch(makeEnvelope("workspace.create", { workspace: "wsp_mem1" }));
    c2.dispatch(makeEnvelope("workspace.create", { workspace: "wsp_mem2" }));
    assert.equal(c1.workspaces.size, 1);
    assert.equal(c2.workspaces.size, 1);
    assert.notDeepEqual(
      Array.from(c1.workspaces.keys()),
      Array.from(c2.workspaces.keys()));
  });

  // Cleanup
  test("cleanup", () => {
    rmSync(tmp, { recursive: true, force: true });
  });
});
