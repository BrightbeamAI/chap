/**
 * Regression: a single unreadable row must not discard every other workspace.
 * SqliteStore.load() skips a corrupt row and returns the rest. Guards the 0.2.9
 * fix. Skipped when better-sqlite3 is unavailable.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let sqliteAvailable = true;
try {
  require("better-sqlite3");
} catch {
  sqliteAvailable = false;
}

describe("SqliteStore corrupt-row recovery", { skip: !sqliteAvailable }, () => {
  test("load skips a corrupt row and keeps the rest", async () => {
    const { SqliteStore } = await import("../src/storage/sqlite.js");
    const tmp = mkdtempSync(join(tmpdir(), "chap-corrupt-"));
    const store = new SqliteStore(join(tmp, "c.db"));
    for (const w of ["w1", "w2", "w3"]) {
      store.save({ id: w, data: { id: w }, version: 1, updated_at: "t" });
    }
    const Database = require("better-sqlite3");
    const raw = new Database(join(tmp, "c.db"));
    raw.prepare("UPDATE chap_workspaces SET data = ? WHERE id = ?").run("{bad json", "w2");
    raw.close();
    assert.deepEqual(store.load().map(r => r.id).sort(), ["w1", "w3"]);
    rmSync(tmp, { recursive: true, force: true });
  });
});
