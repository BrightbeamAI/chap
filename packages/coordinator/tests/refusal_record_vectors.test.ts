/**
 * The shared refusal-recording vectors, replayed against this reference.
 *
 * Every response and every recorded entry is compared whole, and the chain
 * head after the call is compared too, which pins the bytes a refusal's link
 * hashes. The Python suite reads the same file, so a drift in one reference
 * fails in both. conformance/refusal-record-vectors.md says what each case
 * covers.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Coordinator } from "../src/coordinator.ts";

const VECTORS = JSON.parse(readFileSync(
  new URL("../../../conformance/refusal-record-vectors.json", import.meta.url), "utf8",
)).vectors as Array<{
  name: string; profiles: string[]; workspace: string; setup: unknown[];
  refused_setup?: unknown[]; refused_setup_at?: number;
  envelope: unknown; response: unknown; recorded: boolean; audit_count: number; refusal_count: number;
  evidence_head: string; entry?: unknown;
}>;

for (const vector of VECTORS) {
  test(`${vector.name}: the refusal and the log are the recorded ones`, () => {
    const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                                defaultProfiles: vector.profiles } as never);
    // refused_setup is sent after the first refused_setup_at setup envelopes,
    // and each of those calls is refused.
    const refusedFirst = vector.refused_setup ?? [];
    const at = vector.refused_setup_at ?? vector.setup.length;
    const base = vector.setup.slice(0, at);
    const rest = vector.setup.slice(at);
    for (const envelope of base) {
      const r = c.dispatch(JSON.parse(JSON.stringify(envelope)) as never) as any;
      assert.equal(r.error, undefined, (envelope as any).method);
    }
    for (const envelope of refusedFirst) {
      const r = c.dispatch(JSON.parse(JSON.stringify(envelope)) as never) as any;
      assert.notEqual(r.error, undefined, (envelope as any).method);
    }
    for (const envelope of rest) {
      const r = c.dispatch(JSON.parse(JSON.stringify(envelope)) as never) as any;
      assert.equal(r.error, undefined, (envelope as any).method);
    }
    assert.deepEqual(c.dispatch(JSON.parse(JSON.stringify(vector.envelope)) as never), vector.response);

    const entries = (c.dispatch({ jsonrpc: "2.0", id: "r", method: "audit.read",
      params: { workspace: vector.workspace, from: "human:a" } } as never) as any).result.entries;
    assert.equal(entries.length, vector.audit_count);
    assert.equal(entries.filter((e: any) => e.outcome !== undefined).length, vector.refusal_count);
    if (vector.recorded) {
      // Compared as text too, so the order of keys is pinned as well.
      assert.equal(JSON.stringify(entries[entries.length - 1]), JSON.stringify(vector.entry));
    }
    assert.equal((c.workspaces.get(vector.workspace) as any).chain_head, vector.evidence_head);
  });
}

test("the fixture covers both halves", () => {
  // A file of recorded refusals alone would pass against a coordinator that
  // recorded every refusal.
  const recorded = VECTORS.filter(v => v.recorded);
  assert.ok(recorded.length >= 4);
  assert.ok(VECTORS.length - recorded.length >= 4);
});

