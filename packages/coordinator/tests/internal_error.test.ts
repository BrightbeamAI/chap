/**
 * -32603 carries no exception text; the operator gets it through a hook.
 *
 * A handler's exception was answered with its message in `data.detail`, and
 * the message can name a path, a query or a value from another workspace
 * (29 October review). The caller now learns only that the call failed, and
 * onInternalError receives the error, defaulting to stderr.
 *
 * Mirrors packages/coordinator-py/tests/test_internal_error.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator, type CoordinatorOptions } from "../src/coordinator.js";
import type { Envelope } from "../src/types.js";

function failing(options: CoordinatorOptions = {}): Coordinator {
  const c = new Coordinator(options);
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create", params: { workspace: "w" } } as Envelope);
  c.handlers.set("task.create", () => { throw new Error("secret path /srv/app/db.sqlite"); });
  return c;
}

const call = { jsonrpc: "2.0", id: "2", method: "task.create",
  params: { workspace: "w", from: "human:a", kind: "k", input: {} } } as Envelope;

test("the caller is answered with -32603 and no detail", () => {
  const seen: unknown[] = [];
  const c = failing({ onInternalError: (err, at) => { seen.push([err, at]); } });
  assert.deepEqual(c.dispatch(structuredClone(call)),
    { jsonrpc: "2.0", id: "2", error: { code: -32603, message: "Internal error" } });
  assert.equal(seen.length, 1);
  const [err, at] = seen[0] as [Error, unknown];
  assert.equal(err.message, "secret path /srv/app/db.sqlite");
  assert.deepEqual(at, { method: "task.create", workspace: "w" });
});

test("without a hook the error goes to stderr", () => {
  const c = failing();
  const written: unknown[][] = [];
  const original = console.error;
  console.error = (...args: unknown[]) => { written.push(args); };
  try {
    assert.equal(c.dispatch(structuredClone(call)).error?.code, -32603);
  } finally {
    console.error = original;
  }
  assert.equal(written.length, 1);
  assert.match(String(written[0][0]), /task\.create failed with an internal error/);
  assert.equal((written[0][1] as Error).message, "secret path /srv/app/db.sqlite");
});

test("a hook that throws does not break dispatch", () => {
  const c = failing({ onInternalError: () => { throw new Error("hook failed"); } });
  assert.equal(c.dispatch(structuredClone(call)).error?.code, -32603);
});
