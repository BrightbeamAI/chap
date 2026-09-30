/**
 * A coordinator started on a store brings back every workspace in it.
 *
 * Restoring record by record kept only the last workspace, because each
 * restore replaced what the previous one had loaded. A workspace lost that way
 * is re-created empty by the next join, and its stored log is overwritten.
 *
 * The mirror of this file is
 * packages/coordinator-py/tests/test_rehydrate_every_workspace.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { MemoryStore } from "../src/storage/store.ts";

test("every workspace in the store survives a restart", () => {
  const store = new MemoryStore();
  const c = new Coordinator({ store, deterministicIds: true, deterministicClock: true } as never);
  for (const w of ["w1", "w2", "w3"]) {
    c.dispatch({ jsonrpc: "2.0", id: `c-${w}`, method: "workspace.create",
                 params: { workspace: w, from: "human:a" } } as never);
    c.dispatch({ jsonrpc: "2.0", id: `j-${w}`, method: "participant.join",
                 params: { workspace: w, from: "human:a", type: "human" } } as never);
  }
  const restarted = new Coordinator({ store } as never);
  assert.deepEqual([...restarted.workspaces.keys()].sort(), ["w1", "w2", "w3"]);
  for (const w of ["w1", "w2", "w3"]) {
    assert.equal(restarted.workspaces.get(w)!.audit.length, c.workspaces.get(w)!.audit.length, w);
  }
});
