/**
 * Regression: a revoked signing key must not be usable by backdating the
 * envelope's self-asserted `ts` to before the revocation. Revocation is
 * checked against the coordinator's trusted clock. Guards the 0.2.7 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { canonicalize } from "../src/canonical.ts";
import { deriveKeypair, signEnvelope } from "../src/crypto.ts";

function setup() {
  const c = new Coordinator({ requireSignatures: true });
  const sender = "human:alice";
  const { privateKey, jwk } = deriveKeypair(sender);
  const kid = jwk.kid;
  const signed = (method: string, params: Record<string, unknown>, ts: string) => {
    const env: Record<string, unknown> = { jsonrpc: "2.0", id: method, method, params: { ...params, ts } };
    (env as { sig?: string }).sig = signEnvelope(canonicalize(env as never), privateKey, kid);
    return env;
  };
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
    params: { workspace: "w", from: sender, type: "human", jwks: { keys: [jwk] }, profiles: ["core/1.0", "security-signed/1.0"] } } as never);
  const key = (c.workspaces.get("w") as any).members.get(sender).keys[0];
  key.valid_from = "2026-01-01T00:00:00.000Z";
  return { c, signed, key, sender };
}

test("revoked key cannot be used with a backdated ts", () => {
  const { c, signed, key, sender } = setup();
  key.revoked_at = "2026-06-01T00:00:00.000Z";
  const r = c.dispatch(signed("task.create",
    { workspace: "w", from: sender, kind: "x", input: {}, assignee: sender },
    "2026-03-01T00:00:00.000Z") as never) as { error?: { code: number } };
  assert.ok(r.error && r.error.code === -32072); // SIG_KEY_REVOKED
});

test("non-revoked key verifies at a historical ts", () => {
  const { c, signed, sender } = setup();
  const r = c.dispatch(signed("task.create",
    { workspace: "w", from: sender, kind: "x", input: {}, assignee: sender },
    "2026-03-01T00:00:00.000Z") as never) as { result?: unknown };
  assert.ok("result" in r);
});

test("unverifiable signature is rejected, not skipped", () => {
  // Fail-closed: a present-but-unverifiable signature (unknown workspace)
  // must be rejected under requireSignatures, never silently accepted.
  const c = new Coordinator({ requireSignatures: true });
  const r = c.dispatch({ jsonrpc: "2.0", id: "1", method: "task.create",
    params: { workspace: "nope", from: "human:mallory", kind: "x", input: {}, assignee: "human:mallory" },
    sig: "ed25519:garbage:xxx" } as never) as { error?: { code: number } };
  assert.ok(r.error && r.error.code === -32070); // SIG_VERIFY_FAILED
});
