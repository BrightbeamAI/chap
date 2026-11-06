/**
 * The shared malformed-params vectors, replayed against this reference.
 *
 * Each case is a param of the wrong type or shape that the differential
 * fuzzer found the two references answering differently, or answering with
 * -32603 after changing state. A refused case must be answered as the file
 * says and leave the workspace as it was, its log apart: a refusal the file
 * marks `recorded` adds one entry (SPECIFICATION 10.1). The Python suite
 * reads the same file, so a drift in either reference fails in both.
 *
 * Mirrors packages/coordinator-py/tests/test_malformed_params_vectors.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Coordinator } from "../src/coordinator.js";
import type { Envelope } from "../src/types.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const FILE = JSON.parse(readFileSync(resolve(ROOT, "conformance/malformed-params-vectors.json"), "utf-8"));

function replay(): Coordinator {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true, defaultProfiles: FILE.profiles });
  for (const env of FILE.setup) {
    const r = c.dispatch(structuredClone(env));
    assert.equal(r.error, undefined, `${env.method}: ${JSON.stringify(r.error)}`);
  }
  return c;
}

/** Every workspace without its log, and the length of each log. */
function state(c: Coordinator): { rest: string; logs: number[] } {
  const all = c.snapshot() as Array<Record<string, unknown>>;
  const logs = all.map((ws) => (ws.audit as unknown[]).length);
  const rest = all.map(({ audit: _audit, chain_head: _head, ...ws }) => ws);
  return { rest: JSON.stringify(rest), logs };
}

for (const vector of FILE.refused) {
  test(`refused: ${vector.name}`, () => {
    const c = replay();
    const before = state(c);
    const response = c.dispatch(structuredClone(vector.envelope) as Envelope);
    assert.deepEqual(response, vector.response);
    const after = state(c);
    assert.equal(after.rest, before.rest, "a refused call changed the workspace");
    assert.deepEqual(after.logs, before.logs.map((n) => n + (vector.recorded ? 1 : 0)));
  });
}

for (const vector of FILE.accepted) {
  test(`accepted: ${vector.name}`, () => {
    const c = replay();
    for (const env of vector.before ?? []) {
      assert.equal(c.dispatch(structuredClone(env)).error, undefined);
    }
    assert.deepEqual(c.dispatch(structuredClone(vector.envelope) as Envelope), vector.response);
  });
}
