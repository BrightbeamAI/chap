/**
 * Pluggable persistence for the Coordinator.
 *
 * A Store persists workspace state between process restarts. Coordinators
 * call `store.save(workspaceId, snapshot)` after every mutation that
 * appends to the audit chain, and `store.load()` on startup to restore.
 *
 * Two implementations ship in-tree:
 *   - MemoryStore  (default, no persistence; existing behaviour)
 *   - SqliteStore  (real SQLite via better-sqlite3, opt-in)
 *
 * Third-party stores (Postgres, Redis, etc.) implement this interface.
 *
 * Concurrency model
 * -----------------
 *
 * A Coordinator is a **single-writer** component: dispatch is serial, workspace
 * state is held in memory, and each chain link is computed from the head the
 * instance holds. This interface is a persistence mechanism, not a
 * concurrency-control one. `save` is an unconditional write and no
 * implementation rejects a stale record.
 *
 * Running two or more Coordinator instances against one shared store is
 * therefore not supported. A stale instance's write replaces a newer one
 * wholesale: entries written by the other instance are lost, the chain is
 * re-linked from the surviving head, and `audit.verify_chain` still reports
 * `ok`, because the surviving chain is internally consistent. The loss is
 * silent and CHAP's own verification will not reveal it.
 *
 * If you need more than one instance, serialise writes above the Coordinator by
 * partitioning workspaces so no workspace is ever written by two, or by holding
 * an external lock. See SPECIFICATION.md section 10.3.
 *
 * Stores hold per-workspace JSON snapshots, not a decomposed relational
 * model. The Coordinator's snapshot/restore methods produce JSON-safe
 * payloads; this is fast for workspaces in the low-thousands of audit
 * entries and trades query flexibility for simplicity. Production
 * deployments needing rich query semantics should project the audit log
 * into their own analytics store via the `onAudit` listener.
 */

export interface WorkspaceRecord {
  /** Workspace identifier. */
  id: string;
  /** JSON-serialisable payload produced by `Coordinator.snapshot()`. */
  data: unknown;
  /**
   * Incremented on each save. Recorded for diagnostics and for stores that
   * want it; it is NOT enforced. Nothing here performs a compare-and-swap,
   * so this field does not give you optimistic concurrency. See the
   * concurrency model in this module's header.
   */
  version: number;
  /** Wall-clock timestamp of the last write, ISO-8601. */
  updated_at: string;
}

export interface Store {
  /** Load all workspace records into memory. Called once on coordinator start. */
  load(): Promise<WorkspaceRecord[]> | WorkspaceRecord[];

  /** Persist one workspace record. Called after every mutation. */
  save(record: WorkspaceRecord): Promise<void> | void;

  /** Remove one workspace. Used by `workspace.delete` semantics (not in v0.2). */
  delete(id: string): Promise<void> | void;

  /** Release resources. Idempotent. */
  close?(): Promise<void> | void;
}

/**
 * In-memory store. Default behaviour: no persistence, fast tests.
 * State lives in a Map; closing it discards everything.
 */
export class MemoryStore implements Store {
  private records = new Map<string, WorkspaceRecord>();

  load(): WorkspaceRecord[] {
    return Array.from(this.records.values());
  }

  save(record: WorkspaceRecord): void {
    this.records.set(record.id, record);
  }

  delete(id: string): void {
    this.records.delete(id);
  }

  close(): void {
    this.records.clear();
  }
}
