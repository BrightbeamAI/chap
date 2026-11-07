/**
 * SqliteStore through node:sqlite, the binding Node 22.13 and later ship,
 * where better-sqlite3 is not installed. The same file reads the same through
 * either binding, and every write the store makes goes through the calls the
 * two have in common, with `pragma` and `transaction` written over them for
 * node:sqlite.
 *
 * Before 0.3.5 a SqliteStore needed better-sqlite3, a native module that
 * cannot be built everywhere.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Coordinator } from "../src/coordinator.js";
import { SqliteStore } from "../src/storage/sqlite.js";
import { StoreConflictError } from "../src/storage/store.js";

const require_ = createRequire(import.meta.url);
let nodeSqlite = true;
try { require_("node:sqlite"); } catch { nodeSqlite = false; }
let betterSqlite = true;
try { require_("better-sqlite3"); } catch { betterSqlite = false; }

const OPTIONS = { deterministicIds: true, deterministicClock: true, enableChain: true };

function exercise(c: Coordinator) {
  let n = 0;
  const send = (method: string, from: string, params: Record<string, unknown> = {}) =>
    c.dispatch({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from, ...params } }) as any;
  send("workspace.create", "human:a");
  send("participant.join", "human:a", { type: "human" });
  send("participant.join", "agent:b", { type: "agent" });
  const id = send("task.create", "human:a", { kind: "k", input: {}, assignee: "agent:b", review_required: true }).result.task_id;
  send("task.complete", "agent:b", { task_id: id, output: { draft: 1 } });
  return send;
}

const plain = (c: Coordinator) => JSON.parse(JSON.stringify(c.snapshot()));

test("node:sqlite", { skip: !nodeSqlite }, async (t) => {
  const tmp = mkdtempSync(join(tmpdir(), "chap-node-sqlite-"));
  try {
    await t.test("a coordinator writes and restores a file through it", () => {
      const path = join(tmp, "node.db");
      const store = new SqliteStore(path, { driver: "node:sqlite" });
      const c = new Coordinator({ ...OPTIONS, store });
      exercise(c);
      store.close();
      const reopened = new SqliteStore(path, { driver: "node:sqlite" });
      assert.deepEqual(plain(new Coordinator({ ...OPTIONS, store: reopened })), plain(c));
      assert.equal(reopened.version("w"), c.workspaces.get("w")!.audit.length);
      assert.equal(reopened.version("none"), undefined);
      reopened.close();
    });

    await t.test("a write that does not follow what the file holds is refused, and nothing is written", () => {
      const store = new SqliteStore(join(tmp, "conflict.db"), { driver: "node:sqlite" });
      exercise(new Coordinator({ ...OPTIONS, store }));
      const held = store.version("w")!;
      assert.throws(() => store.apply({ id: "w", base: held - 1, version: held, updated_at: "t",
                                        meta: { id: "w" }, entries: [{ seq: held - 1 }], objects: {} }),
                    (err: unknown) => err instanceof StoreConflictError);
      assert.equal(store.version("w"), held);
      store.close();
    });

    await t.test("the file reads the same through either binding", { skip: !betterSqlite }, () => {
      const path = join(tmp, "both.db");
      const written = new SqliteStore(path, { driver: "better-sqlite3" });
      const c = new Coordinator({ ...OPTIONS, store: written });
      exercise(c);
      written.close();
      const other = new SqliteStore(path, { driver: "node:sqlite" });
      const d = new Coordinator({ ...OPTIONS, store: other });
      assert.deepEqual(plain(d), plain(c));
      // Written through node:sqlite, read back through better-sqlite3.
      d.dispatch({ jsonrpc: "2.0", id: "x", method: "participant.join",
                   params: { workspace: "w", from: "human:c", type: "human" } });
      other.close();
      const back = new SqliteStore(path, { driver: "better-sqlite3" });
      assert.deepEqual(plain(new Coordinator({ ...OPTIONS, store: back })), plain(d));
      back.close();
    });

    await t.test("a file from a release before 0.3.2 is moved into the tables", () => {
      const path = join(tmp, "old.db");
      const source = new Coordinator(OPTIONS);
      exercise(source);
      const { DatabaseSync } = require_("node:sqlite");
      const old = new DatabaseSync(path);
      old.exec(`CREATE TABLE chap_workspaces (id TEXT PRIMARY KEY, data TEXT NOT NULL,
        version INTEGER NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX idx_chap_workspaces_updated_at ON chap_workspaces (updated_at);`);
      old.prepare("INSERT INTO chap_workspaces VALUES (?, ?, 7, 't1')")
        .run("w", JSON.stringify((source.snapshot() as unknown[])[0]));
      old.close();
      const store = new SqliteStore(path, { driver: "node:sqlite" });
      assert.deepEqual(plain(new Coordinator({ ...OPTIONS, store })), plain(source));
      const kept = store.db.prepare("SELECT id FROM chap_workspaces_v1").all() as Array<{ id: string }>;
      assert.deepEqual(kept.map((r) => r.id), ["w"]);
      store.close();
    });

    await t.test("a write waits for another process's lock, as better-sqlite3's does", async () => {
      // node:sqlite waits for no lock unless told to, so a write that met
      // another process's transaction failed at once.
      const path = join(tmp, "busy.db");
      const store = new SqliteStore(path, { driver: "node:sqlite" });
      const c = new Coordinator({ ...OPTIONS, store });
      c.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create", params: { workspace: "w" } });
      const holder = spawn(process.execPath, ["-e", `
        const { DatabaseSync } = require("node:sqlite");
        const db = new DatabaseSync(${JSON.stringify(path)});
        db.exec("BEGIN IMMEDIATE");
        process.stdout.write("held\\n");
        setTimeout(() => { db.exec("COMMIT"); db.close(); }, 400);
      `], { stdio: ["ignore", "pipe", "inherit"] });
      await once(holder.stdout!, "data");
      const r = c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
                             params: { workspace: "w", from: "agent:a", type: "agent" } }) as any;
      await once(holder, "exit");
      assert.deepEqual(r.result, { joined: true, as: "agent:a" });
      store.close();
    });
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});
