/**
 * dispatch answers whatever it is given, and never throws.
 *
 * A body of `null` reached `env.id` and threw, so a server that passed the
 * parsed body straight to dispatch, as two of the reference servers did,
 * stopped answering. Anything that is not a JSON-RPC call is answered with
 * -32600, and a response's id is null where the request carries none, as the
 * Python reference answers.
 *
 * Mirrors packages/coordinator-py/tests/test_dispatch_any_value.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import type { Envelope } from "../src/types.js";

const INVALID = { code: -32600, message: "Invalid JSON-RPC 2.0 request" };

test("a value that is not a call is answered with -32600 and a null id", () => {
  const c = new Coordinator();
  for (const value of [null, undefined, [], "x", 5, true, {}, { jsonrpc: "1.0", method: "x" }]) {
    const r = c.dispatch(value as unknown as Envelope);
    assert.deepEqual(r, { jsonrpc: "2.0", id: null, error: INVALID }, JSON.stringify(value));
  }
});

test("an invalid call keeps the id it carries", () => {
  const c = new Coordinator();
  assert.deepEqual(c.dispatch({ jsonrpc: "2.0", id: "x1" } as Envelope),
    { jsonrpc: "2.0", id: "x1", error: INVALID });
});

test("a call with no id is answered with a null id", () => {
  const c = new Coordinator();
  const r = c.dispatch({ jsonrpc: "2.0", method: "workspace.create", params: { workspace: "w" } } as Envelope);
  assert.equal(r.id, null);
  assert.ok("result" in r);
});
