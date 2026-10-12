/**
 * Pluggable persistence for the Coordinator.
 *
 * A Store keeps workspace state between process restarts. The Coordinator
 * calls `store.load()` once at start, and writes after every call that adds
 * to a workspace's audit log.
 *
 * How the Coordinator writes depends on what the store implements:
 *
 *   - `apply(change)`: the Coordinator sends what the call changed: the new
 *     audit entries, the objects it touched and the workspace's own fields.
 *     The cost of a write stays flat as the log grows; it grows with the
 *     objects a call changes, such as a task with a long history. MemoryStore
 *     and SqliteStore implement it.
 *   - `save(record)` alone: the Coordinator sends the whole workspace on every
 *     write, as releases before 0.3.2 did, so the cost of a write grows with
 *     the log. Stores written for earlier releases keep working this way.
 *
 * MemoryStore is the default and keeps nothing across processes. SqliteStore
 * keeps state in a SQLite file through better-sqlite3. Third-party stores
 * (Postgres, Redis and so on) implement this interface.
 *
 * Concurrency model
 * -----------------
 *
 * A Coordinator is a **single-writer** component: dispatch is serial,
 * workspace state is held in memory, and each chain link is computed from the
 * head the instance holds. Run one Coordinator per workspace.
 *
 * Every change names the number of audit entries the Coordinator expects the
 * store to hold already (`base`). MemoryStore and SqliteStore refuse a change
 * whose base differs from what they hold: they throw a StoreConflictError and
 * write nothing. A second Coordinator writing the same workspace is refused
 * and reported, and the entries already stored stay as they are. The refused
 * instance's memory no longer matches the store; restart it to reload.
 *
 * `save` makes no such check: it replaces what the store holds for the
 * workspace. A store that implements only `save` therefore still loses
 * entries silently when two instances write one workspace.
 *
 * To run more than one instance, partition workspaces so that no workspace is
 * ever written by two, or hold an external lock. See SPECIFICATION.md 10.3.
 *
 * Production deployments that need rich queries over the log can project it
 * into their own analytics store with the `onAudit` listener.
 */

/** The workspace maps a store keeps object by object. */
export const COLLECTIONS = [
  "members", "tasks", "overrides", "whispers",
  "deliberations", "handoffs", "snapshots", "route_decisions",
] as const;

/** task.create idempotency keys, kept as one more collection of key to task id. */
export const IDEMPOTENCY_KEYS = "idempotency_keys";

export interface WorkspaceRecord {
  /** Workspace identifier. */
  id: string;
  /** JSON-serialisable payload in the shape `Coordinator.snapshot()` produces. */
  data: unknown;
  /**
   * For `save`, a counter the Coordinator increments on each write. For a
   * record a store loads after `apply`, the number of audit entries. Recorded
   * for diagnostics; `save` does not check it.
   */
  version: number;
  /** Wall-clock timestamp of the last write, ISO-8601. */
  updated_at: string;
}

/** One write to one workspace: what one or more calls changed. */
export interface StoreChange {
  /** Workspace identifier. */
  id: string;
  /**
   * Audit entries the store must already hold for this workspace. A store
   * that holds a different number refuses the change with a
   * StoreConflictError and writes nothing. Not checked when `replace` is set.
   */
  base: number;
  /** Audit entries the store holds after the change: `base + entries.length`. */
  version: number;
  /** Wall-clock timestamp of the write, ISO-8601. */
  updated_at: string;
  /** The workspace's own fields: everything except its collections and log. */
  meta: Record<string, unknown>;
  /** New audit entries in order. The first has seq `base`. */
  entries: unknown[];
  /**
   * Objects written, by collection and then by key. A null value removes the
   * object. The collections are those in COLLECTIONS and IDEMPOTENCY_KEYS.
   */
  objects: Record<string, Record<string, unknown>>;
  /**
   * Replace everything the store holds for this workspace with this change,
   * which then carries the whole workspace from seq 0.
   */
  replace?: boolean;
}

/**
 * A change whose base does not match the audit entries the store holds.
 * Usually a second Coordinator writing the same workspace.
 */
export class StoreConflictError extends Error {
  readonly workspace: string;
  /** Audit entries the store holds. */
  readonly held: number;
  /** Audit entries the change expected. */
  readonly base: number;

  constructor(workspace: string, held: number, base: number) {
    super(
      `The store holds ${held} audit entries for workspace ${workspace} and ` +
      `this write expected ${base}, so nothing was written. Another ` +
      `coordinator may be writing to this workspace, the store may have been ` +
      `changed outside this coordinator, or this coordinator could not load ` +
      `the workspace when it started. Restart this coordinator to reload.`,
    );
    this.name = "StoreConflictError";
    this.workspace = workspace;
    this.held = held;
    this.base = base;
  }

  /**
   * Matched by name, so `instanceof` holds across the package's entry points,
   * each of which is bundled with its own copy of this class.
   */
  static [Symbol.hasInstance](value: unknown): boolean {
    return value instanceof Error && value.name === "StoreConflictError";
  }
}

export interface Store {
  /** Load all workspace records. Called once when the Coordinator starts. */
  load(): Promise<WorkspaceRecord[]> | WorkspaceRecord[];

  /**
   * Replace what the store holds for one workspace. The Coordinator calls it
   * after every write when the store has no `apply`.
   */
  save(record: WorkspaceRecord): Promise<void> | void;

  /**
   * Write one change. Optional; when present the Coordinator calls it in
   * place of `save`. It must be atomic: the whole change is written, or
   * nothing is. The change refers to the Coordinator's live objects, so
   * serialise or copy everything before returning, including in an async
   * store. Refuse a change whose `base` differs from the entries held, unless
   * `replace` is set, by throwing (or rejecting with) a StoreConflictError.
   */
  apply?(change: StoreChange): Promise<void> | void;

  /** Remove one workspace. */
  delete(id: string): Promise<void> | void;

  /** Release resources. Idempotent. */
  close?(): Promise<void> | void;
}

function isCollection(name: string): boolean {
  return (COLLECTIONS as readonly string[]).includes(name);
}

/** The key an object is stored under in its collection. */
export function objectKey(collection: string, item: unknown): string | undefined {
  if (item === null || typeof item !== "object") return undefined;
  const o = item as Record<string, unknown>;
  const key = collection === "members" ? (o.uri ?? o.id) : (o.id ?? o.uri);
  return typeof key === "string" ? key : undefined;
}

/**
 * A collection as a snapshot holds it, keyed. The TypeScript snapshot holds
 * arrays and the Python snapshot holds objects keyed by id; both are read.
 */
function keyed(collection: string, value: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (Array.isArray(value)) {
    for (const item of value) {
      const key = objectKey(collection, item);
      if (key !== undefined) out[key] = item;
    }
  } else if (value !== null && typeof value === "object") {
    Object.assign(out, value);
  }
  return out;
}

/** A whole-workspace snapshot as a change that replaces what the store holds. */
export function changeFromRecord(record: WorkspaceRecord): StoreChange {
  const data = (record.data !== null && typeof record.data === "object"
    ? record.data : {}) as Record<string, unknown>;
  const meta: Record<string, unknown> = {};
  const objects: Record<string, Record<string, unknown>> = {};
  let entries: unknown[] = [];
  for (const [name, value] of Object.entries(data)) {
    if (name === "audit") entries = Array.isArray(value) ? value : [];
    else if (name === IDEMPOTENCY_KEYS || isCollection(name)) objects[name] = keyed(name, value);
    else meta[name] = value;
  }
  if (meta.id === undefined) meta.id = record.id;
  return {
    id: record.id, base: 0, version: record.version, updated_at: record.updated_at,
    meta, entries, objects, replace: true,
  };
}

/**
 * Rows read back from a store, assembled into the snapshot shape
 * `Coordinator.restore()` reads: arrays for the collections, an object for
 * the idempotency keys, and the log in seq order.
 */
export function assembleSnapshot(
  meta: Record<string, unknown>,
  entries: unknown[],
  objects: Iterable<[collection: string, key: string, value: unknown]>,
): Record<string, unknown> {
  const data: Record<string, unknown> = { ...meta };
  for (const name of COLLECTIONS) data[name] = [];
  const keys: Record<string, unknown> = {};
  data[IDEMPOTENCY_KEYS] = keys;
  for (const [collection, key, value] of objects) {
    if (collection === IDEMPOTENCY_KEYS) {
      keys[key] = value;
    } else {
      const list = (Array.isArray(data[collection]) ? data[collection] : (data[collection] = [])) as unknown[];
      list.push(value);
    }
  }
  data.audit = entries;
  return data;
}

interface Held {
  meta: string;
  version: number;
  updated_at: string;
  entries: string[];
  objects: Map<string, Map<string, string>>;
}

/**
 * In-memory store. The default: it keeps nothing across processes. Like a
 * database it keeps each row as JSON text, so a Coordinator restarted on it
 * sees what was written and nothing changed later.
 */
export class MemoryStore implements Store {
  private held = new Map<string, Held>();

  load(): WorkspaceRecord[] {
    const out: WorkspaceRecord[] = [];
    for (const [id, h] of this.held) {
      const objects: Array<[string, string, unknown]> = [];
      for (const [collection, items] of h.objects) {
        for (const [key, value] of items) objects.push([collection, key, JSON.parse(value)]);
      }
      out.push({
        id,
        data: assembleSnapshot(JSON.parse(h.meta), h.entries.map(e => JSON.parse(e)), objects),
        version: h.version,
        updated_at: h.updated_at,
      });
    }
    return out;
  }

  save(record: WorkspaceRecord): void {
    this.apply(changeFromRecord(record));
  }

  apply(change: StoreChange): void {
    // Serialised before anything is held, so a change that cannot be written
    // leaves the store as it was.
    const meta = JSON.stringify(change.meta);
    const entries = change.entries.map(e => JSON.stringify(e));
    const objects = Object.entries(change.objects).map(([collection, items]) =>
      [collection, Object.entries(items).map(([key, value]) =>
        [key, value === null || value === undefined ? null : JSON.stringify(value)] as const)] as const);
    let h = this.held.get(change.id);
    if (!change.replace) {
      const holds = h ? h.entries.length : 0;
      if (holds !== change.base) throw new StoreConflictError(change.id, holds, change.base);
    }
    if (!h || change.replace) {
      h = { meta: "{}", version: 0, updated_at: "", entries: [], objects: new Map() };
    }
    for (const entry of entries) h.entries.push(entry);
    for (const [collection, items] of objects) {
      let held = h.objects.get(collection);
      if (!held) h.objects.set(collection, (held = new Map()));
      for (const [key, value] of items) {
        if (value === null) held.delete(key);
        else held.set(key, value);
      }
    }
    h.meta = meta;
    h.version = change.version;
    h.updated_at = change.updated_at;
    this.held.set(change.id, h);
  }

  delete(id: string): void {
    this.held.delete(id);
  }
}
