/**
 * A self-asserted jwks is never registered for an identity-bound participant.
 * When an OIDC/VC join pins a cnf.jwk, that verifier-attested key is the only
 * signing key, and a join that brings another key is refused, so the key
 * cannot be used to sign as that identity.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function verifyToken(t: string) {
  return t === "alice-token"
    ? { sub: "alice", chap_participant_uri: "human:alice", auth_time: 1747476000,
        cnf: { jwk: { kty: "OKP", crv: "Ed25519", kid: "device-key", x: "DK" } } }
    : null;
}

test("a join that brings a key other than the one its token binds is refused", () => {
  const c = new Coordinator({ deterministicIds: true, verifyOidcToken: verifyToken });
  const join = (jwks?: unknown) => c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join", params: {
    workspace: "w", from: "human:alice", type: "human", oidc_token: "alice-token",
    ...(jwks ? { jwks } : {}) } } as never) as any;
  assert.deepEqual(join({ keys: [{ kty: "OKP", crv: "Ed25519", kid: "attacker", x: "EVIL" }] }).error,
    { code: -32404, message: "The join's keys are not the key the token binds" });
  assert.equal(join().error, undefined);
  const m = (c.workspaces.get("w") as any).members.get("human:alice");
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["device-key"]);
});

test("self-asserted jwks are registered without an identity binding", () => {
  const c = new Coordinator({ deterministicIds: true });
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join", params: {
    workspace: "w", from: "agent:b", type: "agent",
    jwks: { keys: [{ kid: "k1", x: "K" }] } } } as never);
  const m = (c.workspaces.get("w") as any).members.get("agent:b");
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["k1"]);
});
