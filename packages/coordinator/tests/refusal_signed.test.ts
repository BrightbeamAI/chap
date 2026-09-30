/**
 * SPECIFICATION 10.1: what happens when a signed request is sent again.
 *
 * The log publishes every recorded call, signature and all, so anyone who can
 * read it holds a copy of each signed request. A copy of a recorded refusal is
 * answered with that refusal and not evaluated, even once whatever refused it
 * has changed. A copy of a call that took effect is evaluated as any request
 * is, and if it is refused the refusal is not recorded, because its signer made
 * that call once. Both rules compare what the sender signed, the request
 * without its `sig`, so re-encoding a signature does not make a new request.
 *
 * The mirror of this file is
 * packages/coordinator-py/tests/test_refusal_signed.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { canonicalize } from "../src/canonical.ts";
import { deriveKeypair, publicKeyFromJwk, signEnvelope, verifyEnvelope } from "../src/crypto.ts";

const PROFILES = ["core/1.0", "review/1.0", "control/1.0", "security-signed/1.0", "audit-scitt/1.0"];
const PEOPLE: Array<[string, string]> = [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"]];

function signedReady() {
  const c = new Coordinator({ requireSignatures: true, deterministicIds: true, deterministicClock: true,
                              defaultProfiles: PROFILES } as never);
  const keys = new Map(PEOPLE.map(([uri]) => [uri, deriveKeypair(uri)]));
  let n = 0;
  const envelope = (method: string, params: Record<string, unknown> = {}, from = "human:a", id = `s${++n}`) => {
    const env: Record<string, unknown> = { jsonrpc: "2.0", id, method, params: { workspace: "w", from, ...params } };
    const { privateKey, jwk } = keys.get(from)!;
    env.sig = signEnvelope(canonicalize(env as never), privateKey, jwk.kid);
    return env;
  };
  const send = (method: string, params: Record<string, unknown> = {}, from = "human:a"): any =>
    c.dispatch(envelope(method, params, from) as never);
  const copy = (env: unknown): any => c.dispatch(JSON.parse(JSON.stringify(env)) as never);
  c.dispatch({ jsonrpc: "2.0", id: "create", method: "workspace.create",
               params: { workspace: "w", from: "human:a", profiles: PROFILES } } as never);
  for (const [uri, type] of PEOPLE) {
    c.dispatch({ jsonrpc: "2.0", id: `join-${uri}`, method: "participant.join",
                 params: { workspace: "w", from: uri, type, jwks: { keys: [keys.get(uri)!.jwk] } } } as never);
  }
  const ws = c.workspaces.get("w") as any;
  const underReview = (to: string): string => {
    const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").result.task_id;
    send("task.update", { task_id: id, state: "in_progress" }, "agent:b");
    send("review.request", { task_id: id, artefact: { body: "draft" }, to }, "agent:b");
    return id;
  };
  return { c, keys, envelope, send, copy, ws, underReview };
}

/**
 * The same signature in a different encoding. A 64-byte signature leaves four
 * unused bits in its last base64 character, and a lenient decoder ignores them.
 */
function reencoded(sig: string): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const i = sig.length - 3;
  return sig.slice(0, i) + alphabet[alphabet.indexOf(sig[i]) | 1] + sig.slice(i + 1);
}

test("a signed copy of a recorded refusal is answered with it, even once it would pass", () => {
  const { copy, envelope, send, ws, underReview } = signedReady();
  const id = underReview("human:c");
  const late = envelope("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  assert.equal(copy(late).error.code, -32011);
  const at = ws.audit.length - 1;
  send("review.request", { task_id: id, artefact: { body: "draft" }, to: "human:a" }, "agent:b");
  const before = ws.audit.length;

  const replay = copy(late);
  assert.deepEqual(replay.error, {
    code: -32011,
    message: `Refused at seq ${at}; a refused request is not evaluated again`,
    data: { refused_at_seq: at },
  });
  assert.equal(ws.audit.length, before, "the copy was recorded");
  assert.equal(ws.tasks.get(id).state, "review_requested", "the copy took effect");

  // A retry is a new request: a new id, signed afresh.
  assert.equal(send("decide.approve", { task_id: id, comment: "ok" }, "human:a").error, undefined);
});

test("re-encoding the signature does not make a new request", () => {
  const { copy, envelope, keys, send, ws, underReview } = signedReady();
  const id = underReview("human:c");
  const late = envelope("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  copy(late);
  const at = ws.audit.length - 1;
  send("review.request", { task_id: id, artefact: { body: "draft" }, to: "human:a" }, "agent:b");

  const variant = { ...late, sig: reencoded(late.sig as string) };
  assert.notEqual(variant.sig, late.sig);
  // The variant verifies, so only the rule stands between it and the decision.
  const { sig: _s, ...unsigned } = variant;
  assert.ok(verifyEnvelope(canonicalize(unsigned as never), variant.sig,
                           publicKeyFromJwk(keys.get("human:a")!.jwk as never)));
  assert.deepEqual(copy(variant).error.data, { refused_at_seq: at });
  assert.equal(ws.tasks.get(id).state, "review_requested");
});

test("a copy of a refusal on a paused workspace does not take effect once it resumes", () => {
  const { copy, envelope, send, ws } = signedReady();
  send("control.pause", { scope: "workspace", reason: "incident" }, "human:a");
  const create = envelope("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b");
  assert.equal(copy(create).error.code, -32063);
  const at = ws.audit.length - 1;
  send("control.resume", { scope: "workspace", reason: "clear" }, "human:a");
  const tasks = ws.tasks.size;

  assert.deepEqual(copy(create).error, {
    code: -32063,
    message: `Refused at seq ${at}; a refused request is not evaluated again`,
    data: { refused_at_seq: at },
  });
  assert.equal(ws.tasks.size, tasks);
});

test("the answer comes before the pause and before the signature check", () => {
  const { copy, envelope, send, ws, underReview } = signedReady();
  const id = underReview("human:c");
  const late = envelope("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  copy(late);
  const at = ws.audit.length - 1;
  send("control.pause", { scope: "workspace", reason: "incident" }, "human:c");
  assert.deepEqual(copy(late).error.data, { refused_at_seq: at });
  ws.members.get("human:a").keys[0].revoked_at = "2000-01-01T00:00:00.000Z";
  assert.deepEqual(copy(late).error.data, { refused_at_seq: at });
});

test("a signed copy of an accepted call that is refused is not recorded", () => {
  const { copy, envelope, ws, underReview } = signedReady();
  const id = underReview("human:c");
  const approve = envelope("decide.approve", { task_id: id, comment: "ok" }, "human:c");
  assert.equal(copy(approve).error, undefined);
  const before = ws.audit.length;
  // The review is closed, so the copy is refused. Its signer approved once.
  assert.equal(copy(approve).error.code, -32010);
  assert.equal(copy({ ...approve, sig: reencoded(approve.sig as string) }).error.code, -32010);
  assert.equal(ws.audit.length, before);
});

test("the rules survive a snapshot and a restore", () => {
  const { c, copy, envelope, send, ws, underReview } = signedReady();
  const id = underReview("human:c");
  const late = envelope("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  copy(late);
  const at = ws.audit.length - 1;
  const approve = envelope("decide.approve", { task_id: id, comment: "ok" }, "human:c");
  copy(approve);
  send("control.pause", { scope: "workspace", reason: "incident" }, "human:c");

  // The restored coordinator runs on the real clock, which is past the time
  // the deterministic clock registered the keys at.
  const d = new Coordinator({ requireSignatures: true, defaultProfiles: PROFILES } as never);
  d.restore(JSON.parse(JSON.stringify(c.snapshot())));
  const restored = d.workspaces.get("w") as any;
  const before = restored.audit.length;
  assert.deepEqual((d.dispatch(JSON.parse(JSON.stringify(late)) as never) as any).error.data, { refused_at_seq: at });
  assert.equal((d.dispatch(JSON.parse(JSON.stringify(approve)) as never) as any).error.code, -32063);
  assert.equal(restored.audit.length, before);
});

test("a refusal for a key that is unknown, revoked or the wrong one for a rotation is not recorded", () => {
  const { c, send, ws } = signedReady();
  const before = ws.audit.length;

  const stranger = deriveKeypair("human:nobody");
  const unknown: Record<string, unknown> = { jsonrpc: "2.0", id: "k1", method: "task.create",
    params: { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "agent:b" } };
  unknown.sig = signEnvelope(canonicalize(unknown as never), stranger.privateKey, stranger.jwk.kid);
  assert.equal((c.dispatch(unknown as never) as any).error.code, -32071);

  const other = deriveKeypair("human:a#second");
  const rotate = send("participant.rotate_key",
    { old_kid: other.jwk.kid, new_jwk: other.jwk }, "human:a");
  assert.equal(rotate.error.code, -32073);

  ws.members.get("human:a").keys[0].revoked_at = "2000-01-01T00:00:00.000Z";
  assert.equal(send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "human:a").error.code, -32072);
  assert.equal(ws.audit.length, before);
});

test("an unsigned refusal does not answer a signed call with the same content", () => {
  // Only signed calls are indexed, so a refusal anyone could have sent in the
  // member's name cannot stand in for the member's signed call.
  const { c, envelope, ws, underReview } = signedReady();
  const id = underReview("human:c");
  const late = envelope("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  const { sig: _s, ...unsigned } = late;
  // The unsigned copy is refused -32070, since signatures are required, and
  // is not recorded. Record an unsigned refusal directly to plant it.
  ws.audit.push({ seq: ws.audit.length, arrived: "2026-01-01T00:00:00.000Z",
                  request: unsigned, outcome: { status: "refused", code: -32011 } });
  const d = new Coordinator({ requireSignatures: true, defaultProfiles: PROFILES } as never);
  d.restore(JSON.parse(JSON.stringify(c.snapshot())));
  const r = d.dispatch(JSON.parse(JSON.stringify(late)) as never) as any;
  assert.equal(r.error.code, -32011);
  assert.equal(r.error.data, undefined, "the unsigned refusal answered the signed call");
});

test("a signed call that cannot be canonicalised is refused first, not answered from the log", () => {
  const { c, keys, ws } = signedReady();
  const env: Record<string, unknown> = { jsonrpc: "2.0", id: "f", method: "task.create",
    params: { workspace: "w", from: "human:a", kind: "k", input: { weight: 1.5 }, assignee: "agent:b" } };
  env.sig = "ed25519:" + keys.get("human:a")!.jwk.kid + ":" + "A".repeat(86) + "==";
  const before = ws.audit.length;
  assert.equal((c.dispatch(env as never) as any).error.code, -32602);
  assert.equal(ws.audit.length, before);
});
