/**
 * Every refusal the patch engine can give, in both references, word for word.
 *
 * A divergence here is a divergence in the response the two coordinators
 * return for the same envelope. JavaScript's JSON.stringify and Python's repr
 * quote differently, and the two runtimes name their own types differently, so
 * the same refusal read two ways until both were made to speak JSON. Found by
 * the differential fuzzer once its overrides drew from the whole operation set.
 *
 * The Python side of this file is test_patch_message_parity.py. The cases
 * below and the cases there are the same list in the same order.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { applyJsonPatch, PatchError } from "../src/patch.ts";

const CASES: [string, unknown, unknown[]][] = [
  ["array index with a leading zero", { items: [1, 2] },
   [{ op: "replace", path: "/items/01", value: 1 }]],
  ["array index in exponent form", { items: [1, 2] },
   [{ op: "replace", path: "/items/1e1", value: 1 }]],
  ["array index with a separator", { items: [1, 2] },
   [{ op: "replace", path: "/items/1_0", value: 1 }]],
  ["add into a string", { a: "str" },
   [{ op: "add", path: "/a/b", value: 1 }]],
  ["add into a number", { a: { b: 3 } },
   [{ op: "add", path: "/a/b/c", value: 1 }]],
  ["replace at an index of an object", { a: { x: 1 } },
   [{ op: "replace", path: "/a/0", value: 1 }]],
  ["remove from a string", { a: "str" },
   [{ op: "remove", path: "/a/x" }]],
  ["index past the end", { items: [1] },
   [{ op: "remove", path: "/items/5" }]],
  ["an operation that does not exist", { a: 1 },
   [{ op: "frobnicate", path: "/a" }]],
  ["a pointer with no leading slash", { a: 1 },
   [{ op: "add", path: "a", value: 1 }]],
  ["a prototype-polluting segment", {},
   [{ op: "add", path: "/__proto__", value: 1 }]],
  ["test against the wrong value", { a: 1 },
   [{ op: "test", path: "/a", value: 2 }]],
  ["move into a child of itself", { a: { b: {} } },
   [{ op: "move", from: "/a", path: "/a/b/c" }]],
  // RFC 6901: "-" names the position after the last element, which no
  // element occupies, so only "add" may use it.
  ["remove at the position after the last element", { a: [1, 2] },
   [{ op: "remove", path: "/a/-" }]],
  ["replace at the position after the last element", { a: [1, 2] },
   [{ op: "replace", path: "/a/-", value: 9 }]],
  ["test at the position after the last element", { a: [1, 2] },
   [{ op: "test", path: "/a/-", value: 2 }]],
  ["copy from the position after the last element", { a: [1, 2], b: 0 },
   [{ op: "copy", from: "/a/-", path: "/b" }]],
];

const VECTORS = new URL("../../../conformance/patch-message-vectors.json", import.meta.url);

test("every case is refused, with the recorded message", () => {
  const recorded = JSON.parse(readFileSync(VECTORS, "utf8")).messages as Record<string, string>;
  const got: Record<string, string> = {};
  for (const [name, doc, diff] of CASES) {
    assert.throws(() => applyJsonPatch(doc, diff as never), PatchError, name);
    try { applyJsonPatch(doc, diff as never); } catch (e) { got[name] = (e as Error).message; }
  }
  assert.deepEqual(got, recorded);
});
