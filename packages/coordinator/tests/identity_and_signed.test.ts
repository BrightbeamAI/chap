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

test("participant.revoke_key marks the key revoked", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const k = genKeypair();
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "wsp_rv" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "wsp_rv", from: "human:alice", type: "human", role: "owner",
              jwks: { keys: [k.jwk] }}});
  const r = c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.revoke_key",
    params: { workspace: "wsp_rv", from: "human:admin",
              target_uri: "human:alice", kid: k.jwk.kid, reason: "test" }});
  assert.ok((r.result as { revoked: boolean }).revoked);
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
