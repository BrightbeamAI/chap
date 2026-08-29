import { test } from "node:test";
import assert from "node:assert/strict";

import { applyJsonPatch } from "./server.ts";

test("applyJsonPatch refuses __proto__ segments (prototype pollution)", () => {
  assert.throws(() => applyJsonPatch({ a: 1 }, [
    { op: "add", path: "/__proto__/polluted", value: "yes" } as never,
  ]));
  // Object.prototype must be untouched.
  assert.equal(({} as Record<string, unknown>).polluted, undefined);
});

test("applyJsonPatch refuses constructor/prototype segments", () => {
  assert.throws(() => applyJsonPatch({ a: 1 }, [
    { op: "replace", path: "/constructor/prototype/x", value: 1 } as never,
  ]));
  assert.equal(({} as Record<string, unknown>).x, undefined);
});

test("applyJsonPatch still applies a normal patch", () => {
  const out = applyJsonPatch({ severity: "warning" }, [
    { op: "replace", path: "/severity", value: "info" } as never,
  ]);
  assert.deepEqual(out, { severity: "info" });
});
