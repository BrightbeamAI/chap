/**
 * The shared dispatch-gate vectors, replayed against this reference.
 *
 * Every response is compared whole. A refusal that changed its message, its
 * code or the detail in `data` would be a change to what a client sees, and
 * the Python suite reads the same file, so a drift in one reference fails in
 * both. conformance/profile-gate-vectors.md says what each case covers.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Coordinator } from "../src/coordinator.ts";

const VECTORS = JSON.parse(readFileSync(
  new URL("../../../conformance/profile-gate-vectors.json", import.meta.url), "utf8",
)).vectors as Array<{
  name: string; profiles: string[]; setup: unknown[]; envelope: unknown; response: unknown;
}>;

for (const vector of VECTORS) {
  test(`${vector.name}: the response is the recorded one`, () => {
    const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                                defaultProfiles: vector.profiles } as never);
    for (const envelope of vector.setup) {
      const r = c.dispatch(envelope as never) as any;
      assert.equal(r.error, undefined, (envelope as any).method);
    }
    assert.deepEqual(c.dispatch(vector.envelope as never), vector.response);
  });
}

test("the fixture covers both halves", () => {
  // A file of refusals alone would pass against a coordinator that refused
  // everything.
  const refused = VECTORS.filter(v => (v.response as any).error?.code === -32601);
  assert.ok(refused.length >= 4);
  assert.ok(VECTORS.length - refused.length >= 4);
});
