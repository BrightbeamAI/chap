/**
 * Tests for security-signed/1.0 + identity-oidc/1.0 + identity-vc/1.0.
 * Mirrors packages/coordinator-py/tests/test_identity_and_signed.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, KeyObject } from "node:crypto";

import {
  Coordinator,
  canonicalize,
  signEnvelope,
  publicKeyBytes,
} from "../src/index.js";
import { b64urlNoPad } from "../src/crypto.js";
import type { Envelope } from "../src/index.js";

function genKeypair(): { sk: KeyObject; pubRaw: Buffer; jwk: { kty: "OKP"; crv: "Ed25519"; kid: string; x: string } } {
  const { privateKey } = generateKeyPairSync("ed25519");
  const pubRaw = publicKeyBytes(privateKey);
  const kid = "k-" + Math.random().toString(36).slice(2, 10);
  return {
    sk: privateKey,
    pubRaw,
    jwk: { kty: "OKP", crv: "Ed25519", kid, x: b64urlNoPad(pubRaw) },
  };
}

function signed(env: Envelope, sk: KeyObject, kid: string): Envelope {
  const stripped = JSON.parse(JSON.stringify(env)) as Envelope;
  delete stripped.sig;
  env.sig = signEnvelope(canonicalize(stripped), sk, kid);
  return env;
}

// ============================================================
//   security-signed/1.0
// ============================================================

test("correctly signed envelope verifies", () => {
  const c = new Coordinator({ requireSignatures: true,
    deterministicIds: true, deterministicClock: true });
  const alice = genKeypair();
  const bot = genKeypair();
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "wsp_s" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "wsp_s", from: "human:alice", type: "human", role: "owner",
              jwks: { keys: [alice.jwk] }}});
  c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.join",
    params: { workspace: "wsp_s", from: "agent:bot", type: "agent", role: "drafter",
              jwks: { keys: [bot.jwk] }}});

  const env: Envelope = { jsonrpc: "2.0", id: "4", method: "task.create",
    params: { workspace: "wsp_s", from: "human:alice", kind: "draft",
              input: {}, assignee: "agent:bot" }};
  signed(env, alice.sk, alice.jwk.kid);
  const r = c.dispatch(env);
  assert.ok("result" in r && !r.error);
});

test("envelope without sig is rejected when requireSignatures is on", () => {
  const c = new Coordinator({ requireSignatures: true,
    deterministicIds: true, deterministicClock: true });
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "wsp_s" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "wsp_s", from: "human:alice", type: "human", role: "owner" }});
  c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.join",
    params: { workspace: "wsp_s", from: "agent:bot", type: "agent", role: "d" }});
  const r = c.dispatch({ jsonrpc: "2.0", id: "4", method: "task.create",
    params: { workspace: "wsp_s", from: "human:alice", kind: "k",
              input: {}, assignee: "agent:bot" }});
  assert.equal(r.error?.code, -32070);  // SIG_VERIFY_FAILED
});

test("tampered envelope after signing fails verify", () => {
  const c = new Coordinator({ requireSignatures: true,
    deterministicIds: true, deterministicClock: true });
  const alice = genKeypair();
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "wsp_s" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "wsp_s", from: "human:alice", type: "human", role: "owner",
              jwks: { keys: [alice.jwk] }}});
  c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.join",
    params: { workspace: "wsp_s", from: "agent:bot", type: "agent", role: "d" }});

  const env: Envelope = { jsonrpc: "2.0", id: "4", method: "task.create",
    params: { workspace: "wsp_s", from: "human:alice", kind: "k",
              input: { orig: true }, assignee: "agent:bot" }};
  signed(env, alice.sk, alice.jwk.kid);
  // tamper after signing
  (env.params as Record<string, unknown>).input = { tampered: true };
  const r = c.dispatch(env);
  assert.equal(r.error?.code, -32070);
});

test("participant.rotate_key sets valid_until on old key", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const k1 = genKeypair();
  const k2 = genKeypair();
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "wsp_r" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "wsp_r", from: "human:alice", type: "human", role: "owner",
              jwks: { keys: [k1.jwk] }}});
  const r = c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.rotate_key",
    params: { workspace: "wsp_r", from: "human:alice",
              old_kid: k1.jwk.kid, new_jwk: k2.jwk }});
  assert.ok((r.result as { rotated: boolean }).rotated);
  const member = c.workspaces.get("wsp_r")!.members.get("human:alice")!;
  const old = member.keys.find(k => k.kid === k1.jwk.kid)!;
  assert.ok(old.valid_until !== undefined);
});

test("participant.rotate_key must be signed with the old key", () => {
  const c = new Coordinator({ requireSignatures: true });
  const k1 = genKeypair();
  const k2 = genKeypair();
  const k3 = genKeypair();
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "w" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human",
              jwks: { keys: [k1.jwk, k2.jwk] },
              profiles: ["core/1.0", "security-signed/1.0"] }});

  const rotate = (sk: KeyObject, kid: string) =>
    c.dispatch(signed({ jsonrpc: "2.0", id: "r", method: "participant.rotate_key",
      params: { workspace: "w", from: "human:alice",
                old_kid: k1.jwk.kid, new_jwk: k3.jwk }}, sk, kid));

  assert.equal((rotate(k2.sk, k2.jwk.kid).error as { code: number }).code, -32073);
  assert.ok((rotate(k1.sk, k1.jwk.kid).result as { rotated: boolean }).rotated);
});

test("participant.revoke_key marks the key revoked", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const k = genKeypair();
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "wsp_rv" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "wsp_rv", from: "human:alice", type: "human", role: "owner",
              jwks: { keys: [k.jwk] }}});
  c.dispatch({ jsonrpc: "2.0", id: "2a", method: "participant.join",
    params: { workspace: "wsp_rv", from: "human:admin", type: "human", role: "admin" }});
  const r = c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.revoke_key",
    params: { workspace: "wsp_rv", from: "human:admin",
              target_uri: "human:alice", kid: k.jwk.kid, reason: "test" }});
  assert.ok((r.result as { revoked: boolean }).revoked);
});

test("participant.revoke_key requires self or admin", () => {
  const c = new Coordinator({});
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "w" }});
  for (const who of ["human:alice", "human:bob"]) {
    const g = genKeypair();
    c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
      params: { workspace: "w", from: who, type: "human", jwks: { keys: [{ ...g.jwk, kid: who }] }}});
  }
  const revoke = (from: string, target: string) =>
    c.dispatch({ jsonrpc: "2.0", id: "r", method: "participant.revoke_key",
      params: { workspace: "w", from, target_uri: target, kid: target }});

  assert.equal((revoke("human:bob", "human:alice").error as { code: number }).code, -32011);
  assert.ok((revoke("human:alice", "human:alice").result as { revoked: boolean }).revoked);
});

// ============================================================
//   identity-oidc/1.0
// ============================================================

test("OIDC token binding pins cnf.jwk", () => {
  const c = new Coordinator({
    deterministicIds: true, deterministicClock: true,
    verifyOidcToken: (t) => {
      if (t === "good") return {
        sub: "user-123", auth_time: 1747476000,
        cnf: { jwk: { kty: "OKP", crv: "Ed25519", kid: "oidc-key", x: "AA" }},
      };
      return null;
    },
  });
  const r = c.dispatch({ jsonrpc: "2.0", id: "1", method: "participant.join",
    params: { workspace: "wsp_o", from: "human:alice", type: "human",
              role: "r", oidc_token: "good" }});
  assert.equal((r.result as { joined: boolean }).joined, true);
  const member = c.workspaces.get("wsp_o")!.members.get("human:alice")!;
  assert.equal(member.oidc_sub, "user-123");
  assert.equal(member.oidc_auth_time, 1747476000);
  assert.ok(member.keys.some(k => k.kid === "oidc-key"));
});

test("OIDC invalid token returns -32403", () => {
  const c = new Coordinator({
    deterministicIds: true, deterministicClock: true,
    verifyOidcToken: () => null,
  });
  const r = c.dispatch({ jsonrpc: "2.0", id: "1", method: "participant.join",
    params: { workspace: "wsp_o", from: "human:alice", type: "human",
              role: "r", oidc_token: "bad" }});
  assert.equal(r.error?.code, -32403);
});

// ============================================================
//   identity-vc/1.0
// ============================================================

test("VC presentation pins holder key", () => {
  const c = new Coordinator({
    deterministicIds: true, deterministicClock: true,
    verifyVc: (vp) => {
      if (vp.type === "VerifiablePresentation") return {
        holder: "did:example:alice",
        cnf_jwk: { kty: "OKP", crv: "Ed25519", kid: "vc-key", x: "BB" },
      };
      return null;
    },
  });
  const r = c.dispatch({ jsonrpc: "2.0", id: "1", method: "participant.join",
    params: { workspace: "wsp_v", from: "human:alice", type: "human", role: "r",
              vc_presentation: { type: "VerifiablePresentation" }}});
  assert.equal((r.result as { joined: boolean }).joined, true);
  const m = c.workspaces.get("wsp_v")!.members.get("human:alice")!;
  assert.equal(m.vc_holder, "did:example:alice");
  assert.ok(m.keys.some(k => k.kid === "vc-key"));
});

test("VC invalid presentation returns -32410", () => {
  const c = new Coordinator({
    deterministicIds: true, deterministicClock: true,
    verifyVc: () => null,
  });
  const r = c.dispatch({ jsonrpc: "2.0", id: "1", method: "participant.join",
    params: { workspace: "wsp_v", from: "human:alice", type: "human", role: "r",
              vc_presentation: { type: "Bogus" }}});
  assert.equal(r.error?.code, -32410);
});

test("a rotated-out key cannot sign live requests via a backdated ts", () => {
  const c = new Coordinator({ requireSignatures: true,
    deterministicIds: true, deterministicClock: true });
  const k1 = genKeypair();
  const k2 = genKeypair();
  const bot = genKeypair();
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "w" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human", role: "owner",
              jwks: { keys: [k1.jwk] }, profiles: ["core/1.0", "security-signed/1.0"] }});
  c.dispatch({ jsonrpc: "2.0", id: "2b", method: "participant.join",
    params: { workspace: "w", from: "agent:bot", type: "agent", role: "drafter",
              jwks: { keys: [bot.jwk] }}});

  // Rotate alice's key (signed with the old key); this sets valid_until on k1.
  const rot: Envelope = { jsonrpc: "2.0", id: "3", method: "participant.rotate_key",
    params: { workspace: "w", from: "human:alice", old_kid: k1.jwk.kid, new_jwk: k2.jwk }};
  signed(rot, k1.sk, k1.jwk.kid);
  assert.ok((c.dispatch(rot).result as { rotated: boolean }).rotated);

  const k1rec = c.workspaces.get("w")!.members.get("human:alice")!
    .keys.find(k => k.kid === k1.jwk.kid)!;
  assert.ok(k1rec.valid_until !== undefined);

  // The holder of the rotated-out key backdates ts into the old key's validity
  // window (valid_from is always inside it) and signs a live request. It must
  // be rejected: the old key is no longer valid per the trusted clock,
  // regardless of the self-asserted ts.
  const env: Envelope = { jsonrpc: "2.0", id: "4", method: "task.create",
    params: { workspace: "w", from: "human:alice", kind: "draft", input: {},
              assignee: "agent:bot", ts: k1rec.valid_from }};
  signed(env, k1.sk, k1.jwk.kid);
  assert.equal(c.dispatch(env).error?.code, -32071);  // SIG_KEY_NOT_FOUND
});

test("a signature that cannot be verified fails closed with SIG_VERIFY_FAILED", () => {
  const c = new Coordinator({ requireSignatures: true });
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create", params: { workspace: "w" }});
  // A structurally valid JWK whose x decodes to the wrong length, so building
  // the public key throws during verification.
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human",
              jwks: { keys: [{ kty: "OKP", crv: "Ed25519", kid: "k1", x: "AA" }] },
              profiles: ["core/1.0", "security-signed/1.0"] }});
  const env: Envelope = { jsonrpc: "2.0", id: "3", method: "task.create",
    params: { workspace: "w", from: "human:alice", kind: "k", input: {}, assignee: "human:alice" },
    sig: "ed25519:k1:AAAA" };
  assert.equal(c.dispatch(env).error?.code, -32070);  // SIG_VERIFY_FAILED, not INTERNAL
});
