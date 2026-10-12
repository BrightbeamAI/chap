import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { MemoryStore, type Store, type StoreChange, type WorkspaceRecord } from "../src/storage/store.ts";

class SlowStore implements Store {
  issued: number[] = [];
  completed: number[] = [];
  last: WorkspaceRecord | undefined;

  load(): WorkspaceRecord[] {
    return [];
  }

  save(record: WorkspaceRecord): Promise<void> {
    this.issued.push(record.version);
    const delay = Math.max(0, (50 - record.version) * 4);
    return new Promise<void>(resolve => {
      setTimeout(() => {
        this.completed.push(record.version);
        this.last = record;
        resolve();
      }, delay);
    });
  }

  delete(): void {}
}

function drive(c: Coordinator) {
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  const tid = s("task.create", { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  s("task.update", { workspace: "w", from: "agent:b", task_id: tid, state: "in_progress" });
  s("task.complete", { workspace: "w", from: "agent:b", task_id: tid, output: {}, confidence: 1 });
}

test("async store saves settle in issue order so the newest write wins", async () => {
  const store = new SlowStore();
  const c = new Coordinator({ deterministicIds: true, store });
  drive(c);
  await c.drainSaves();
  assert.deepEqual(store.completed, store.issued);
  assert.equal(store.last?.version, store.issued[store.issued.length - 1]);
});

test("a store rejection is reported instead of silently swallowed", async () => {
  const seen: Array<{ id: string; version: number }> = [];
  const failing: Store = {
    load: () => [],
    save: () => Promise.reject(new Error("disk full")),
    delete: () => {},
  };
  const c = new Coordinator({
    deterministicIds: true,
    store: failing,
    onStoreError: (_err, record) => { seen.push(record); },
  });
  drive(c);
  await c.drainSaves();
  assert.ok(seen.length > 0);
  assert.equal(seen[0].id, "w");
});

/** An async store with `apply`: it copies the change at once and writes it later. */
class SlowApply extends MemoryStore {
  issued: number[] = [];
  completed: number[] = [];
  failures = 0;

  override apply(change: StoreChange): Promise<void> {
    this.issued.push(change.version);
    const copy = JSON.parse(JSON.stringify(change)) as StoreChange;
    const fail = this.failures > 0;
    if (fail) this.failures--;
    const delay = Math.max(0, (50 - change.version) * 2);
    return new Promise<void>((resolve, reject) => {
      setTimeout(() => {
        if (fail) return reject(new Error("disk full"));
        super.apply(copy);
        this.completed.push(change.version);
        resolve();
      }, delay);
    });
  }
}

const plain = (c: Coordinator) => JSON.parse(JSON.stringify(c.snapshot()));

test("async applies run one after another and the store ends as memory is", async () => {
  const store = new SlowApply();
  const c = new Coordinator({ deterministicIds: true, store });
  drive(c);
  await c.drainSaves();
  assert.deepEqual(store.completed, store.issued);
  assert.deepEqual(plain(new Coordinator({ store })), plain(c));
});

test("a rejected async apply is reported and carried by the next write", async () => {
  const store = new SlowApply();
  const seen: Array<{ id: string; version: number }> = [];
  const c = new Coordinator({ deterministicIds: true, store, onStoreError: (_e, r) => { seen.push(r); } });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  await c.drainSaves();
  store.failures = 1;
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  await c.drainSaves();
  assert.equal(seen.length, 1);
  s("participant.join", { workspace: "w", from: "human:c", type: "human" });
  await c.drainSaves();
  assert.deepEqual(plain(new Coordinator({ store })), plain(c));
});

test("a write queued before restore() leaves the restored workspace's own write to follow", async () => {
  const store = new SlowApply();
  const c = new Coordinator({ deterministicIds: true, store });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "agent:a", type: "agent" });
  c.restore(JSON.parse(JSON.stringify(c.snapshot())));
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  await c.drainSaves();
  assert.deepEqual(plain(new Coordinator({ store })), plain(c));
});
