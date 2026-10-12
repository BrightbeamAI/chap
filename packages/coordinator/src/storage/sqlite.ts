/**
 * SQLite-backed store using better-sqlite3.
 *
 * Schema, shared with the Python SqliteStore. Both write these tables, and
 * the same audit entries for the same calls; the objects are each
 * coordinator's own, so each restores a file it wrote, and chap-analytics
 * reads a file from either:
 *
 *   CREATE TABLE chap_meta (
 *     workspace  TEXT PRIMARY KEY,
 *     data       TEXT NOT NULL,    -- the workspace's own fields, JSON
 *     version    INTEGER NOT NULL, -- audit entries held
 *     updated_at TEXT NOT NULL     -- ISO-8601
 *   );
 *   CREATE TABLE chap_entries (
 *     workspace TEXT NOT NULL,
 *     seq       INTEGER NOT NULL,
 *     data      TEXT NOT NULL,     -- one audit entry, JSON
 *     PRIMARY KEY (workspace, seq)
 *   );
 *   CREATE TABLE chap_objects (
 *     workspace  TEXT NOT NULL,
 *     collection TEXT NOT NULL,    -- members, tasks, ..., idempotency_keys
 *     id         TEXT NOT NULL,
 *     data       TEXT NOT NULL,    -- one object, JSON
 *     PRIMARY KEY (workspace, collection, id)
 *   );
 *
 * A write adds the call's entries, updates the objects the call changed and
 * the workspace's own fields, in one transaction, so its cost does not grow
 * with the log. A write whose first entry is not the next seq the file holds
 * is refused with a StoreConflictError; see the concurrency model in
 * ./store.ts. Audit entries are only ever inserted, except when a workspace
 * is written whole: after `Coordinator.restore()`, and for a workspace an
 * application put in place itself.
 *
 * Files written by releases before 0.3.2 hold one JSON snapshot per workspace
 * in a table named chap_workspaces. Opening such a file moves each workspace
 * into the tables above, in one transaction, and renames the old table to
 * chap_workspaces_v1, which is kept as it was.
 *
 * A view named chap_workspaces takes the old table's place, with the same
 * columns, so a script that read the old table reads the current state; it
 * assembles each workspace when read, with collections keyed by id. Releases
 * before 0.3.2 refuse to open the file, since they cannot write to it.
 *
 * Usage:
 *   import { Coordinator } from "@brightbeamai/chap-coordinator";
 *   import { SqliteStore } from "@brightbeamai/chap-coordinator/storage/sqlite";
 *
 *   const coord = new Coordinator({
 *     store: new SqliteStore("./chap.db"),
 *   });
 *
 * Pass `":memory:"` for an in-memory SQLite instance (faster than the
 * MemoryStore for tests that need transactional semantics).
 *
 * better-sqlite3 is declared as an optionalDependency. If the binary
 * cannot be built (rare on Linux/macOS, common on locked-down CI), the
 * SqliteStore import will throw with a clear message and applications
 * can fall back to MemoryStore or another implementation.
 */

import { createRequire } from "node:module";
import {
  COLLECTIONS, IDEMPOTENCY_KEYS, StoreConflictError, assembleSnapshot, changeFromRecord,
  type Store, type StoreChange, type WorkspaceRecord,
} from "./store.js";

// better-sqlite3 has no published types in its own package, but the
// `@types/better-sqlite3` package provides them. We require a structural
// subset so the file type-checks even if the typings are absent.
interface SqliteDatabase {
  exec(sql: string): unknown;
  prepare(sql: string): SqliteStatement;
  pragma(name: string, options?: { simple?: boolean }): unknown;
  transaction<A extends unknown[], R>(fn: (...args: A) => R): SqliteTransaction<A, R>;
  close(): void;
}
interface SqliteStatement {
  run(...params: unknown[]): { changes: number; lastInsertRowid: number | bigint };
  get(...params: unknown[]): unknown;
  all(...params: unknown[]): unknown[];
}
interface SqliteTransaction<A extends unknown[], R = void> {
  (...args: A): R;
  immediate(...args: A): R;
}

export interface SqliteStoreOptions {
  /**
   * Enable WAL journal mode for better concurrent-read performance.
   * Default true. Set false for `:memory:` databases or when the
   * filesystem does not support WAL (some network filesystems).
   */
  wal?: boolean;
}

const SCHEMA = `
  CREATE TABLE IF NOT EXISTS chap_meta (
    workspace  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    updated_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS chap_entries (
    workspace TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    data      TEXT NOT NULL,
    PRIMARY KEY (workspace, seq)
  );
  CREATE TABLE IF NOT EXISTS chap_objects (
    workspace  TEXT NOT NULL,
    collection TEXT NOT NULL,
    id         TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (workspace, collection, id)
  );
`;

/** The table that releases before 0.3.2 wrote, one snapshot per workspace. */
const LEGACY_TABLE = "chap_workspaces";

/** A read-only view in the old table's place, for scripts that read it. */
const LEGACY_VIEW = `CREATE VIEW IF NOT EXISTS ${LEGACY_TABLE} AS SELECT m.workspace AS id, json_set(m.data, ` +
  [
    "'$.audit', json((SELECT json_group_array(json(e.data)) FROM " +
      "(SELECT data FROM chap_entries WHERE workspace = m.workspace ORDER BY seq) AS e))",
    ...[...COLLECTIONS, IDEMPOTENCY_KEYS].map(c =>
      `'$.${c}', json((SELECT json_group_object(id, json(data)) FROM chap_objects ` +
      `WHERE workspace = m.workspace AND collection = '${c}'))`),
  ].join(", ") +
  ") AS data, m.version AS version, m.updated_at AS updated_at FROM chap_meta AS m";

export class SqliteStore implements Store {
  readonly db: SqliteDatabase;
  private readonly stmt: Record<string, SqliteStatement>;
  private readonly txWrite: SqliteTransaction<[StoreChange]>;
  private readonly txDelete: SqliteTransaction<[string]>;

  constructor(path: string, options: SqliteStoreOptions = {}) {
    let Database: new (path: string) => SqliteDatabase;
    try {
      // Resolved at construction so the dependency stays optional: importing
      // this module costs nothing if you never build a SqliteStore.
      //
      // createRequire rather than a bare require(). A bare one is undefined in
      // an ES module, so from source it threw ReferenceError and this catch
      // reported the dependency as missing when it was installed. The bundled
      // build masked it by shimming require, which is why it went unnoticed.
      // The CommonJS build has no import.meta.url, and resolves from
      // __filename, which an ES module does not define.
      const here = typeof __filename === "string" ? __filename : import.meta.url;
      Database = createRequire(here)("better-sqlite3");
    } catch (err) {
      const detail = err instanceof Error ? `: ${err.message.split("\n")[0]}` : "";
      throw new Error(
        "SqliteStore requires the `better-sqlite3` package. " +
        "Install it with `npm install better-sqlite3` " +
        `or use MemoryStore for non-persistent workloads${detail}`,
      );
    }

    this.db = new Database(path);
    if (path !== ":memory:" && options.wal !== false) {
      this.db.pragma("journal_mode = WAL");
    }
    this.db.exec(SCHEMA);

    const prepare = (sql: string) => this.db.prepare(sql);
    this.stmt = {
      held:       prepare("SELECT MAX(seq) AS last FROM chap_entries WHERE workspace = ?"),
      hasMeta:    prepare("SELECT 1 FROM chap_meta WHERE workspace = ?"),
      hasTable:   prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"),
      hasName:    prepare("SELECT 1 FROM sqlite_master WHERE name = ?"),
      putEntry:   prepare("INSERT INTO chap_entries (workspace, seq, data) VALUES (?, ?, ?)"),
      putObject:  prepare(
        "INSERT INTO chap_objects (workspace, collection, id, data) VALUES (?, ?, ?, ?) " +
        "ON CONFLICT(workspace, collection, id) DO UPDATE SET data = excluded.data"),
      dropObject: prepare("DELETE FROM chap_objects WHERE workspace = ? AND collection = ? AND id = ?"),
      putMeta:    prepare(
        "INSERT INTO chap_meta (workspace, data, version, updated_at) VALUES (?, ?, ?, ?) " +
        "ON CONFLICT(workspace) DO UPDATE SET data = excluded.data, " +
        "version = excluded.version, updated_at = excluded.updated_at"),
      dropMeta:    prepare("DELETE FROM chap_meta WHERE workspace = ?"),
      dropEntries: prepare("DELETE FROM chap_entries WHERE workspace = ?"),
      dropObjects: prepare("DELETE FROM chap_objects WHERE workspace = ?"),
      metas:   prepare("SELECT workspace, data, version, updated_at FROM chap_meta"),
      entries: prepare("SELECT workspace, seq, data FROM chap_entries ORDER BY workspace, seq"),
      objects: prepare("SELECT workspace, collection, id, data FROM chap_objects ORDER BY rowid"),
    };
    this.txWrite = this.db.transaction((change: StoreChange) => this.write(change));
    this.txDelete = this.db.transaction((id: string) => this.drop(id));
    this.open();
  }

  /**
   * In one transaction, so processes opening the file at once agree: move the
   * snapshots a release before 0.3.2 wrote into the tables above, then put the
   * view in the old table's place.
   */
  private open(): void {
    let kept = "";
    const unreadable: string[] = [];
    this.db.transaction(() => {
      if (this.stmt.hasTable.get(LEGACY_TABLE)) {
        const rows = this.db.prepare(
          `SELECT id, data, version, updated_at FROM ${LEGACY_TABLE}`,
        ).all() as Array<{ id: string; data: string; version: number; updated_at: string }>;
        for (const r of rows) {
          // A workspace already in the new tables was written later; it stays.
          if (this.stmt.hasMeta.get(r.id)) continue;
          let data: unknown;
          try { data = JSON.parse(r.data); } catch { unreadable.push(r.id); continue; }
          const change = changeFromRecord({ id: r.id, data, version: r.version, updated_at: r.updated_at });
          this.write({ ...change, version: change.entries.length });
        }
        kept = `${LEGACY_TABLE}_v1`;
        for (let n = 2; this.stmt.hasName.get(kept); n++) kept = `${LEGACY_TABLE}_v1_${n}`;
        // With legacy_alter_table on, a view or trigger of the application's
        // own that reads the old table keeps reading it by name, so it reads
        // the view below and stays current.
        const legacy = this.db.pragma("legacy_alter_table", { simple: true });
        this.db.pragma("legacy_alter_table = ON");
        try {
          this.db.exec(`ALTER TABLE ${LEGACY_TABLE} RENAME TO ${kept}`);
        } finally {
          this.db.pragma(`legacy_alter_table = ${legacy ? "ON" : "OFF"}`);
        }
        // The old index moved with the table. Without it, a release before
        // 0.3.2 that opens this file tries to index the view and stops with
        // an error, which is better than starting with nothing loaded.
        this.db.exec("DROP INDEX IF EXISTS idx_chap_workspaces_updated_at");
      }
      if (!this.stmt.hasName.get(LEGACY_TABLE)) this.db.exec(LEGACY_VIEW);
    }).immediate();
    for (const id of unreadable) {
      console.warn(`chap SqliteStore: workspace ${id} could not be read during the upgrade; ` +
        `its row is kept, unchanged, in ${kept}`);
    }
  }

  private write(change: StoreChange): void {
    const id = change.id;
    if (change.replace) {
      this.drop(id);
    } else {
      const last = (this.stmt.held.get(id) as { last: number | null } | undefined)?.last;
      const held = last === null || last === undefined ? 0 : last + 1;
      if (held !== change.base) throw new StoreConflictError(id, held, change.base);
    }
    const base = change.replace ? 0 : change.base;
    for (let i = 0; i < change.entries.length; i++) {
      this.stmt.putEntry.run(id, base + i, JSON.stringify(change.entries[i]));
    }
    for (const [collection, items] of Object.entries(change.objects)) {
      for (const [key, value] of Object.entries(items)) {
        if (value === null || value === undefined) this.stmt.dropObject.run(id, collection, key);
        else this.stmt.putObject.run(id, collection, key, JSON.stringify(value));
      }
    }
    this.stmt.putMeta.run(id, JSON.stringify(change.meta), change.version, change.updated_at);
  }

  private drop(id: string): void {
    this.stmt.dropMeta.run(id);
    this.stmt.dropEntries.run(id);
    this.stmt.dropObjects.run(id);
  }

  load(): WorkspaceRecord[] {
    // One read transaction, so a write by another process in the middle of
    // the read cannot leave the log and the objects out of step.
    return this.db.transaction(() => this.readAll())();
  }

  private readAll(): WorkspaceRecord[] {
    // A workspace with any unreadable row is skipped whole, with a warning,
    // so the others still load: restoring part of a workspace would put a
    // log beside objects it does not explain.
    const broken = new Map<string, string>();
    const entries = new Map<string, unknown[]>();
    for (const r of this.stmt.entries.all() as Array<{ workspace: string; seq: number; data: string }>) {
      if (broken.has(r.workspace)) continue;
      let list = entries.get(r.workspace);
      if (!list) entries.set(r.workspace, (list = []));
      if (r.seq !== list.length) { broken.set(r.workspace, `audit entry ${list.length} is missing`); continue; }
      try { list.push(JSON.parse(r.data)); }
      catch { broken.set(r.workspace, `audit entry ${r.seq} is unreadable`); }
    }
    const objects = new Map<string, Array<[string, string, unknown]>>();
    for (const r of this.stmt.objects.all() as Array<{ workspace: string; collection: string; id: string; data: string }>) {
      if (broken.has(r.workspace)) continue;
      let list = objects.get(r.workspace);
      if (!list) objects.set(r.workspace, (list = []));
      try { list.push([r.collection, r.id, JSON.parse(r.data)]); }
      catch { broken.set(r.workspace, `${r.collection} ${r.id} is unreadable`); }
    }
    const out: WorkspaceRecord[] = [];
    for (const r of this.stmt.metas.all() as Array<{ workspace: string; data: string; version: number; updated_at: string }>) {
      let meta: Record<string, unknown> | undefined;
      if (!broken.has(r.workspace)) {
        try { meta = JSON.parse(r.data) as Record<string, unknown>; }
        catch { broken.set(r.workspace, "its own fields are unreadable"); }
      }
      if (!meta || typeof meta !== "object") {
        console.warn(`chap SqliteStore: workspace ${r.workspace} was not loaded: ` +
          `${broken.get(r.workspace) ?? "its own fields are unreadable"}`);
        continue;
      }
      const data = assembleSnapshot(meta, entries.get(r.workspace) ?? [], objects.get(r.workspace) ?? []);
      data.id = r.workspace;
      out.push({ id: r.workspace, data, version: r.version, updated_at: r.updated_at });
    }
    return out;
  }

  save(record: WorkspaceRecord): void {
    this.txWrite.immediate(changeFromRecord(record));
  }

  apply(change: StoreChange): void {
    this.txWrite.immediate(change);
  }

  delete(id: string): void {
    this.txDelete.immediate(id);
  }

  close(): void {
    this.db.close();
  }
}
