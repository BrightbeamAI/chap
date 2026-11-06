/**
 * conformance/store-vectors.json: a store the Python coordinator wrote
 * restores here to the state this coordinator reached from the same calls.
 *
 * The Python coordinator writes a field with no value as null, a history
 * entry's sender as `from_`, and a route decision's own fields in `extra`,
 * and this coordinator restored those as they were: a task's history named
 * no sender, so the authors of a review came out wrong. Mirrors
 * test_store_cross_language.py, which restores the TypeScript snapshot.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Coordinator } from "../src/coordinator.js";
import { MemoryStore } from "../src/storage/store.js";

const VECTORS = JSON.parse(readFileSync(
  new URL("../../../conformance/store-vectors.json", import.meta.url), "utf-8"));
const OPTIONS = { deterministicIds: true, deterministicClock: true, enableChain: true };

function native(): Coordinator {
  const c = new Coordinator(OPTIONS);
  for (const e of VECTORS.envelopes) c.dispatch(e);
  return c;
}

function fromPython(): Coordinator {
  const store = new MemoryStore();
  store.save({ id: "w", data: VECTORS.python, version: VECTORS.python.audit.length, updated_at: "t" });
  return new Coordinator({ ...OPTIONS, store });
}

const plain = (c: Coordinator) => JSON.parse(JSON.stringify(c.snapshot()));

test("this coordinator's snapshot is the vector's", () => {
  assert.deepEqual(plain(native())[0], VECTORS.typescript);
});

test("a store the Python coordinator wrote restores to the state this one reached", () => {
  assert.deepEqual(plain(fromPython()), plain(native()));
});

test("the restored workspace carries on as this one's would", () => {
  const [loaded, own] = [fromPython(), native()];
  const task = [...own.workspaces.get("w")!.tasks.values()].find((t) => t.state === "review_requested")!;
  for (const c of [loaded, own]) {
    const author = c.dispatch({ jsonrpc: "2.0", id: "a", method: "decide.approve",
                                params: { workspace: "w", from: "agent:b", task_id: task.id } }) as any;
    assert.equal(author.error?.code, -32011);
  }
  const approve = { jsonrpc: "2.0" as const, id: "d", method: "decide.approve",
                    params: { workspace: "w", from: "human:c", task_id: task.id } };
  assert.deepEqual(loaded.dispatch(approve), own.dispatch(approve));
  assert.equal(loaded.workspaces.get("w")!.chain_head, own.workspaces.get("w")!.chain_head);
});
