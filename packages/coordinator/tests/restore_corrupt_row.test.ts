/**
 * Regression: a single unreadable row must not discard every other workspace.
 * SqliteStore.load() skips the workspace whose row cannot be read and returns
 * the rest. Guards the 0.2.9 fix, and the same for each table the store writes
 * since 0.3.2. Skipped when better-sqlite3 is unavailable.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Same fault as tests/storage.test.ts had: `require` is undefined under
// `tsx --test`, so the ReferenceError was caught and read as "driver
// missing", skipping this suite on every machine. Opening a database is the
// real probe, since the wrapper resolves even with no native bindings.
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

describe("SqliteStore corrupt-row recovery", { skip: !sqliteAvailable }, () => {
  test("load skips a workspace with a corrupt row and keeps the rest", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.js");
    const tmp = mkdtempSync(join(tmpdir(), "chap-corrupt-"));
    const store = new SqliteStore(join(tmp, "c.db"));
    for (const w of ["w1", "w2", "w3", "w4", "w5"]) {
      store.save({ id: w, data: { id: w, audit: [{ seq: 0 }, { seq: 1 }], tasks: [{ id: "t1" }] },
                   version: 2, updated_at: "t" });
    }
    const Database = require_("better-sqlite3");
    const raw = new Database(join(tmp, "c.db"));
    raw.prepare("UPDATE chap_meta SET data = ? WHERE workspace = ?").run("{bad json", "w2");
    raw.prepare("UPDATE chap_entries SET data = ? WHERE workspace = ? AND seq = 1").run("{bad json", "w3");
    raw.prepare("UPDATE chap_objects SET data = ? WHERE workspace = ?").run("{bad json", "w4");
    raw.prepare("DELETE FROM chap_entries WHERE workspace = ? AND seq = 0").run("w5");
    raw.close();
    const warned: string[] = [];
    const original = console.warn;
    console.warn = (...args: unknown[]) => { warned.push(args.join(" ")); };
    try {
      assert.deepEqual(store.load().map(r => r.id).sort(), ["w1"]);
    } finally {
      console.warn = original;
    }
    for (const w of ["w2", "w3", "w4", "w5"]) {
      assert.ok(warned.some(line => line.includes(`workspace ${w} was not loaded`)), w);
    }
    store.close();
    rmSync(tmp, { recursive: true, force: true });
  });
});
