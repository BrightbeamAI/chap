/**
 * A workspace.create that names no workspace is recorded in the one it makes.
 *
 * The call was recorded only under a workspace its params named, so a
 * workspace whose id the coordinator chose began with no entry. Nothing was
 * written to the store until a later call added one, and a coordinator
 * restarted before then had lost the workspace.
 *
 * Mirrors packages/coordinator-py/tests/test_workspace_create_entry.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { MemoryStore } from "../src/storage/store.js";
import type { Envelope } from "../src/types.js";

const create = { jsonrpc: "2.0", id: "c1", method: "workspace.create", params: { profiles: ["core/1.0"] } } as Envelope;

test("the call is the workspace's first entry", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const id = (c.dispatch(structuredClone(create)).result as { workspace: string }).workspace;
  const audit = c.workspaces.get(id)!.audit;
  assert.equal(audit.length, 1);
  assert.deepEqual(audit[0].envelope, create);
});

test("the workspace survives a restart on its store", () => {
  const store = new MemoryStore();
  const c = new Coordinator({ store, deterministicIds: true, deterministicClock: true });
  const id = (c.dispatch(structuredClone(create)).result as { workspace: string }).workspace;
  const again = new Coordinator({ store });
  assert.ok(again.workspaces.has(id));
  assert.equal(again.workspaces.get(id)!.audit.length, 1);
});
