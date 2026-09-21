/**
 * Regression (#154): whisper.answer must record the envelope as received.
 * Mirrors packages/coordinator-py/tests/test_whisper_answer_signature.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { generateKeyPairSync, KeyObject, createPublicKey, verify as edVerify } from "node:crypto";

import { Coordinator, canonicalize, signEnvelope, publicKeyBytes } from "../src/index.js";
import { b64urlNoPad } from "../src/crypto.js";
import type { Envelope } from "../src/index.js";

function keypair(kid: string) {
  const { privateKey } = generateKeyPairSync("ed25519");
  const pubRaw = publicKeyBytes(privateKey);
  return { sk: privateKey, jwk: { kty: "OKP", crv: "Ed25519", kid, x: b64urlNoPad(pubRaw) } };
}

function sign(env: Envelope, sk: KeyObject, kid: string): Envelope {
  const stripped = JSON.parse(JSON.stringify(env)) as Envelope;
  delete stripped.sig;
  env.sig = signEnvelope(canonicalize(stripped), sk, kid);
  return env;
}

function verifies(sk: KeyObject, env: Envelope): boolean {
  const stripped = JSON.parse(JSON.stringify(env)) as Envelope;
  const sig = stripped.sig as string;
  delete stripped.sig;
  const sigBytes = Buffer.from(sig.split(":")[2], "base64");
  return edVerify(null, canonicalize(stripped), createPublicKey(sk), sigBytes);
}

test("recorded whisper.answer verifies under its own signature", () => {
  const PROFILES = ["core/1.0", "whisper/1.0", "audit-scitt/1.0"];
  const c = new Coordinator({ requireSignatures: true, deterministicIds: true,
    deterministicClock: true, defaultProfiles: PROFILES });
  const a = keypair("k-a");
  const b = keypair("k-b");
  const d = (env: Envelope): any => c.dispatch(env as never);

  d({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "w", profiles: PROFILES } });
  d({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "human:a", type: "human", role: "owner", jwks: { keys: [a.jwk] } } });
  d({ jsonrpc: "2.0", id: "3", method: "participant.join",
    params: { workspace: "w", from: "agent:b", type: "agent", role: "drafter", jwks: { keys: [b.jwk] } } });

  const tid = d(sign({ jsonrpc: "2.0", id: "4", method: "task.create",
    params: { workspace: "w", from: "agent:b", kind: "k", input: {}, assignee: "agent:b" } }, b.sk, "k-b")).result.task_id;
  const wid = d(sign({ jsonrpc: "2.0", id: "5", method: "whisper.ask",
    params: { workspace: "w", from: "agent:b", to: "human:a", task_id: tid, question: "ok?",
              deadline_ms: 60000, default_if_lapsed: "yes" } }, b.sk, "k-b")).result.whisper_id;
  const ans = sign({ jsonrpc: "2.0", id: "6", method: "whisper.answer",
    params: { workspace: "w", from: "human:a", whisper_id: wid, answer: "yes" } }, a.sk, "k-a");
  assert.ok(d(ans).result);

  const ws: any = c.workspaces.get("w");
  const recorded: Envelope = ws.audit.find((e: any) => e.envelope.method === "whisper.answer").envelope;
  assert.ok(!("task_id" in (recorded.params as object)), "envelope was mutated after signing");
  assert.ok(verifies(a.sk, recorded), "recorded whisper.answer does not verify");
});

test("answer is still findable by a task filter", () => {
  const PROFILES = ["core/1.0", "whisper/1.0", "audit-scitt/1.0"];
  const c = new Coordinator({ defaultProfiles: PROFILES });
  const s = (m: string, p: Record<string, unknown>): any =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m, params: { workspace: "w", ...p } } as never);
  s("workspace.create", { profiles: PROFILES });
  s("participant.join", { from: "agent:b", type: "agent" });
  s("participant.join", { from: "human:a", type: "human" });
  const tid = s("task.create", { from: "agent:b", kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  const wid = s("whisper.ask", { from: "agent:b", to: "human:a", task_id: tid, question: "ok?",
    deadline_ms: 60000, default_if_lapsed: "yes" }).result.whisper_id;
  s("whisper.answer", { from: "human:a", whisper_id: wid, answer: "yes" });
  const entries = s("audit.read", { from: "human:a", filter: { task_id: tid } }).result.entries;
  assert.ok(entries.map((e: any) => e.envelope.method).includes("whisper.answer"));
});
