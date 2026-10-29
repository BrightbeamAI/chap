/**
 * The package accepts zod `^3.25 || ^4.0`, so its source uses only the zod
 * API both majors provide. `.loose()` is zod 4 only: under zod 3 it stopped
 * the package at import with "z.object(...).loose is not a function".
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";

const ZOD4_ONLY = [/\.loose\(/, /\blooseObject\(/, /\bstrictObject\(/, /\bz\.toJSONSchema\(/];

test("the source calls no zod API that zod 3 lacks", () => {
  const dir = new URL("../src/", import.meta.url);
  for (const name of readdirSync(dir).filter((f) => f.endsWith(".ts"))) {
    const source = readFileSync(new URL(name, dir), "utf8");
    for (const pattern of ZOD4_ONLY) {
      assert.doesNotMatch(source, pattern, `${name} uses ${pattern}`);
    }
  }
});
