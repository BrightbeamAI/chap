/**
 * Regression: a self-asserted jwks is not co-registered for an identity-bound
 * participant. When an OIDC/VC join pins a cnf.jwk, that verifier-attested key is
 * the only signing key; a key the joiner also supplied is ignored so it cannot be
 * used to sign as that identity. Guards the 0.2.7 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function verifyToken(t: string) {
  return t === "alice-token"
    ? { sub: "alice", auth_time: 1747476000, cnf: { jwk: { kty: "OKP", crv: "Ed25519", kid: "device-key", x: "DK" } } }
    : null;
}

test("self-asserted jwks are ignored when the participant is OIDC-bound", () => {
  const c = new Coordinator({ deterministicIds: true, verifyOidcToken: verifyToken });
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join", params: {
    workspace: "w", from: "human:alice", type: "human", oidc_token: "alice-token",
    jwks: { keys: [{ kty: "OKP", crv: "Ed25519", kid: "attacker", x: "EVIL" }] } } } as never);
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
