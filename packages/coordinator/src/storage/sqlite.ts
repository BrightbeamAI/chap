/**
 * SQLite-backed store using better-sqlite3.
 *
 * Schema:
 *   CREATE TABLE chap_workspaces (
 *     id         TEXT PRIMARY KEY,
 *     data       TEXT NOT NULL,    -- JSON.stringify of snapshot
 *     version    INTEGER NOT NULL,
 *     updated_at TEXT NOT NULL     -- ISO-8601
 *   )
 *
 * Usage:
 *   import { Coordinator } from "@brightbeamai/coordinator";
 *   import { SqliteStore } from "@brightbeamai/coordinator/storage/sqlite";
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

import type { Store, WorkspaceRecord } from "./store.js";

// better-sqlite3 has no published types in its own package, but the
// `@types/better-sqlite3` package provides them. We require a structural
// subset so the file type-checks even if the typings are absent.
interface SqliteDatabase {
  exec(sql: string): unknown;
  prepare(sql: string): SqliteStatement;
  pragma(name: string, options?: { simple?: boolean }): unknown;
  close(): void;
}
interface SqliteStatement {
  run(...params: unknown[]): { changes: number; lastInsertRowid: number | bigint };
  get(...params: unknown[]): unknown;
  all(...params: unknown[]): unknown[];
}

export interface SqliteStoreOptions {
  /**
   * Enable WAL journal mode for better concurrent-read performance.
   * Default true. Set false for `:memory:` databases or when the
   * filesystem does not support WAL (some network filesystems).
   */
  wal?: boolean;
}

export class SqliteStore implements Store {
  readonly db: SqliteDatabase;
  private readonly stmtLoad: SqliteStatement;
  private readonly stmtSave: SqliteStatement;
  private readonly stmtDelete: SqliteStatement;

  constructor(path: string, options: SqliteStoreOptions = {}) {
    let Database: new (path: string) => SqliteDatabase;
    try {
      // require() is used so the dependency stays optional. ESM consumers
      // can still load this file; the actual import is dynamic.
      Database = require("better-sqlite3");
    } catch (err) {
      throw new Error(
        "SqliteStore requires the `better-sqlite3` package. " +
        "Install it with `npm install better-sqlite3` " +
        "or use MemoryStore for non-persistent workloads.",
      );
    }

    this.db = new Database(path);
    if (path !== ":memory:" && options.wal !== false) {
      this.db.pragma("journal_mode = WAL");
    }
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS chap_workspaces (
        id         TEXT PRIMARY KEY,
        data       TEXT NOT NULL,
        version    INTEGER NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_chap_workspaces_updated_at
        ON chap_workspaces (updated_at);
    `);

    this.stmtLoad = this.db.prepare(
      "SELECT id, data, version, updated_at FROM chap_workspaces",
    );
    this.stmtSave = this.db.prepare(
      "INSERT INTO chap_workspaces (id, data, version, updated_at) " +
      "VALUES (?, ?, ?, ?) " +
      "ON CONFLICT(id) DO UPDATE SET " +
      "data = excluded.data, " +
      "version = excluded.version, " +
      "updated_at = excluded.updated_at",
    );
    this.stmtDelete = this.db.prepare(
      "DELETE FROM chap_workspaces WHERE id = ?",
    );
  }

  load(): WorkspaceRecord[] {
    const rows = this.stmtLoad.all() as Array<{
      id: string;
      data: string;
      version: number;
      updated_at: string;
    }>;
    return rows.map(r => ({
      id: r.id,
      data: JSON.parse(r.data),
      version: r.version,
      updated_at: r.updated_at,
    }));
  }

  save(record: WorkspaceRecord): void {
    this.stmtSave.run(
      record.id,
      JSON.stringify(record.data),
      record.version,
      record.updated_at,
    );
  }

  delete(id: string): void {
    this.stmtDelete.run(id);
  }

  close(): void {
    this.db.close();
  }
}
