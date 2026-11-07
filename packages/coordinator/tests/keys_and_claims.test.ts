/**
 * A rotation names a key of its own, and a token's auth_time is kept the
 * same way by both coordinators.
 *
 * Keys are found by kid, so a rotation to a new key under the old key's kid
 * closed the new key with the old one and locked the member out. And an
 * auth_time with a fraction, or one that is a boolean, was kept as given by
 * one coordinator and as an integer by the other, so the two held different
 * member state and answered step-up differently. Mirrors
 * packages/coordinator-py/tests/test_keys_and_claims.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { canonicalize } from "../src/canonical.js";
import { deriveKeypair, signEnvelope } from "../src/crypto.js";
import type { Envelope } from "../src/types.js";

test("a rotation to a key under a kid the member holds is refused", () => {
  const c = new Coordinator({ requireSignatures: true, deterministicIds: true, deterministicClock: true });
  let n = 0;
  const signed = (method: string, from: string, params: Record<string, unknown>, keyOf = from): any => {
    const e = { jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from, ...params } } as Envelope;
    const { privateKey, jwk } = deriveKeypair(keyOf);
    return c.dispatch({ ...e, sig: signEnvelope(canonicalize(e as never), privateKey, jwk.kid) });
  };
  c.dispatch({ jsonrpc: "2.0", id: "w", method: "workspace.create", params: { workspace: "w", from: "human:a" } } as never);
  const { jwk } = deriveKeypair("human:a");
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
               params: { workspace: "w", from: "human:a", type: "human", jwks: { keys: [jwk] } } } as never);
  const other = deriveKeypair("human:a#2").jwk;
  assert.deepEqual(signed("participant.rotate_key", "human:a", { old_kid: jwk.kid, new_jwk: { ...other, kid: jwk.kid } }).error,
    { code: -32602, message: `human:a already holds a key ${jwk.kid}; a new key takes a kid of its own` });
  assert.equal(signed("workspace.describe", "human:a", {}).error, undefined, "the member still signs");
});

test("auth_time is kept in whole seconds, and a boolean or a number past 2^53 is no auth_time", () => {
  for (const [claim, kept] of [[1_700_000_000.75, 1_700_000_000], [true, undefined], [1_700_000_001, 1_700_000_001],
                               [1e21, undefined], [Number.MAX_SAFE_INTEGER + 2, undefined]] as const) {
    const c = new Coordinator({
      deterministicIds: true,
      verifyOidcToken: () => ({ sub: "s", chap_participant_uri: "human:a", auth_time: claim }),
    });
    c.dispatch({ jsonrpc: "2.0", id: "w", method: "workspace.create", params: { workspace: "w", from: "human:a" } } as never);
    const r: any = c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
                                params: { workspace: "w", from: "human:a", type: "human", oidc_token: "t" } } as never);
    assert.equal(r.error, undefined, JSON.stringify(r.error));
    assert.equal(c.workspaces.get("w")!.members.get("human:a")!.oidc_auth_time, kept, String(claim));
  }
});
