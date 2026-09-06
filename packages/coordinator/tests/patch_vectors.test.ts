/**
 * Cross-implementation JSON Patch conformance.
 *
 * Loads the shared vectors in conformance/json-patch-vectors.json and asserts
 * this implementation produces the exact patched document for every `accept`
 * case and throws for every `reject` case (including prototype-pollution
 * paths). The identical vectors are checked by the Python reference, so a
 * decide.override applies to the same result on both sides.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { applyJsonPatch } from "../src/patch.ts";

const here = dirname(fileURLToPath(import.meta.url));
const vectors = JSON.parse(
  readFileSync(resolve(here, "..", "..", "..", "conformance", "json-patch-vectors.json"), "utf-8"),
);

test("accept cases produce the expected document", () => {
  for (const c of vectors.accept) {
    assert.deepEqual(applyJsonPatch(c.doc, c.patch), c.expected, c.name);
  }
});

test("reject cases throw", () => {
  for (const c of vectors.reject) {
    assert.throws(() => applyJsonPatch(c.doc, c.patch), `expected reject: ${c.name}`);
  }
});

test("input document is not mutated", () => {
  const doc = { a: [1, 2], b: { c: 3 } };
  applyJsonPatch(doc, [
    { op: "remove", path: "/a/0" },
    { op: "add", path: "/b/d", value: 4 },
  ]);
  assert.deepEqual(doc, { a: [1, 2], b: { c: 3 } });
});

test("prototype pollution does not leak to Object.prototype", () => {
  assert.throws(() =>
    applyJsonPatch({}, [{ op: "add", path: "/__proto__/polluted", value: "x" }]),
  );
  assert.equal(({} as Record<string, unknown>).polluted, undefined);
});

for (const bad of ["/items/1e1", "/items/0x0a", "/items/3.0", "/items/", "/items/1_0", "/items/01"]) {
  test(`non-canonical array index ${JSON.stringify(bad)} is rejected`, () => {
    // Accepted by JS Number() or Python int() but not both; a strict shared
    // rule must reject them all so the two references agree.
    assert.throws(() => applyJsonPatch({ items: [10, 20, 30] },
      [{ op: "replace", path: bad, value: 99 }] as never));
  });
}

test("canonical array index still applies", () => {
  assert.deepEqual(applyJsonPatch({ items: [10, 20, 30] },
    [{ op: "replace", path: "/items/1", value: 99 }] as never), { items: [10, 99, 30] });
  assert.deepEqual(applyJsonPatch({ items: [10, 20, 30] },
    [{ op: "add", path: "/items/-", value: 40 }] as never), { items: [10, 20, 30, 40] });
});
