/**
 * Cross-implementation canonicalisation conformance.
 *
 * Loads the shared vectors in conformance/canonical-number-vectors.json and
 * asserts this implementation produces the exact canonical bytes for every
 * `accept` case and throws for every `reject` case. The identical vectors are
 * checked by the Python reference, so any divergence in number handling
 * between the two implementations fails here.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { canonicalize } from "../src/canonical.ts";

const here = dirname(fileURLToPath(import.meta.url));
const vectors = JSON.parse(
  readFileSync(
    resolve(here, "..", "..", "..", "conformance", "canonical-number-vectors.json"),
    "utf-8",
  ),
);

test("accept cases produce exact canonical bytes", () => {
  for (const c of vectors.accept) {
    assert.equal(
      canonicalize(c.value).toString("utf-8"),
      c.canonical,
      `canonical(${JSON.stringify(c.value)})`,
    );
  }
});

test("reject cases throw", () => {
  for (const c of vectors.reject) {
    // Parse the raw JSON first (mirrors how an envelope is received), then
    // canonicalise; the disallowed values must throw.
    const value = JSON.parse(c.json);
    assert.throws(() => canonicalize(value), `expected reject: ${c.note} (${c.json})`);
  }
});

test("JSON 2.0 is integer-valued and canonicalises to \"2\"", () => {
  assert.equal(canonicalize(JSON.parse("2.0")).toString("utf-8"), "2");
  assert.equal(canonicalize(2).toString("utf-8"), "2");
});

test("negative zero canonicalises to \"0\"", () => {
  assert.equal(canonicalize(-0).toString("utf-8"), "0");
});
