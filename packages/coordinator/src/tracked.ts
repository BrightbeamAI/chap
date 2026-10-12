/**
 * Change tracking for a workspace's maps.
 *
 * When the store implements `apply`, the Coordinator writes only the objects
 * a call changed. A TrackedMap records which keys may hold a changed object
 * since the last write: every key set or deleted, and, while a call is being
 * handled, every key read with `get` or reached by iterating, because a
 * handler changes an object in place after reading it. Reads outside a call,
 * such as an application listing tasks, record nothing.
 */

export interface Tracker {
  /** Whether anything is recorded: true when the store implements `apply`. */
  enabled: boolean;
  /** Calls being handled. Reads are recorded while it is above zero. */
  depth: number;
}

export class TrackedMap<K, V> extends Map<K, V> {
  /** Keys whose objects may have changed since the last write. */
  readonly dirty = new Set<K>();
  readonly tracker: Tracker;

  constructor(tracker: Tracker) {
    super();
    this.tracker = tracker;
  }

  private reading(): boolean {
    return this.tracker.enabled && this.tracker.depth > 0;
  }

  private touchAll(): void {
    if (this.reading()) for (const key of super.keys()) this.dirty.add(key);
  }

  override get(key: K): V | undefined {
    const value = super.get(key);
    if (value !== undefined && this.reading()) this.dirty.add(key);
    return value;
  }

  override set(key: K, value: V): this {
    if (this.tracker.enabled) this.dirty.add(key);
    return super.set(key, value);
  }

  override delete(key: K): boolean {
    const had = super.delete(key);
    if (had && this.tracker.enabled) this.dirty.add(key);
    return had;
  }

  override clear(): void {
    if (this.tracker.enabled) for (const key of super.keys()) this.dirty.add(key);
    super.clear();
  }

  override values(): MapIterator<V> {
    this.touchAll();
    return super.values();
  }

  override entries(): MapIterator<[K, V]> {
    this.touchAll();
    return super.entries();
  }

  override [Symbol.iterator](): MapIterator<[K, V]> {
    this.touchAll();
    return super[Symbol.iterator]();
  }

  override forEach(callback: (value: V, key: K, map: Map<K, V>) => void, thisArg?: unknown): void {
    this.touchAll();
    super.forEach(callback, thisArg);
  }

  /** Read one value without recording it. */
  peek(key: K): V | undefined {
    return super.get(key);
  }
}

/** Every value of a map, read without recording, whatever kind of map it is. */
export function rawValues<V>(map: Map<unknown, V>): IterableIterator<V> {
  return Map.prototype.values.call(map) as IterableIterator<V>;
}

/** Every entry of a map, read without recording, whatever kind of map it is. */
export function rawEntries<K, V>(map: Map<K, V>): IterableIterator<[K, V]> {
  return Map.prototype.entries.call(map) as IterableIterator<[K, V]>;
}
