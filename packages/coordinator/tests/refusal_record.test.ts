/**
 * SPECIFICATION 10.1: a refused call that is a governed attempt is recorded.
 *
 * The attempt sits on the log under `request`, with an `outcome` beside it,
 * so a reader keyed on `envelope` passes it by instead of replaying it. The
 * chain link hashes the outcome together with the request, so neither half
 * can be altered or stripped undetected.
 *
 * The mirror of this file is
 * packages/coordinator-py/tests/test_refusal_record.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Coordinator, PRIVILEGED_METHODS } from "../src/coordinator.ts";
import { ALWAYS_AVAILABLE, OWNING_PROFILE } from "../src/catalogue.ts";
import { canonicalize } from "../src/canonical.ts";
import { deriveKeypair, publicKeyFromJwk, signEnvelope, verifyEnvelope } from "../src/crypto.ts";

const CHAINED = ["core/1.0", "review/1.0", "control/1.0", "audit-scitt/1.0"];

function ready(profiles: string[] = CHAINED, options: Record<string, unknown> = {}) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                              defaultProfiles: profiles, ...options } as never);
  const envelope = (method: string, params: Record<string, unknown> = {}, from = "human:a") =>
    ({ jsonrpc: "2.0", id: `id-${method}`, method, params: { workspace: "w", from, ...params } });
  const send = (method: string, params: Record<string, unknown> = {}, from = "human:a"): any =>
    c.dispatch(envelope(method, params, from) as never);
  send("workspace.create", { profiles });
  for (const [uri, type] of [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"]]) {
    send("participant.join", { type }, uri);
  }
  const ws = c.workspaces.get("w") as any;
  return { c, send, envelope, ws };
}

/** A task drafted by agent:b and put up for review to human:c alone. */
function underReview(send: (m: string, p?: Record<string, unknown>, f?: string) => any): string {
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").result.task_id;
  send("task.update", { task_id: id, state: "in_progress" }, "agent:b");
  send("review.request", { task_id: id, artefact: { body: "draft" }, to: "human:c" }, "agent:b");
  return id;
}

// ------------------------------------------------------------ what is recorded

test("an unauthorised decision is recorded as a refusal", () => {
  const { send, ws } = ready();
  const id = underReview(send);
  const before = ws.audit.length;
  const r = send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  assert.equal(r.error.code, -32011);
  assert.equal(ws.audit.length, before + 1);
  const entry = ws.audit[before];
  assert.equal(entry.envelope, undefined);
  assert.equal(entry.request.method, "decide.approve");
  assert.deepEqual(entry.outcome, { status: "refused", code: -32011 });
});

test("acting on a paused workspace is recorded as a refusal", () => {
  const { send, ws } = ready();
  send("control.pause", { scope: "workspace", reason: "incident" });
  const before = ws.audit.length;
  const r = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b");
  assert.equal(r.error.code, -32063);
  assert.equal(ws.audit.length, before + 1);
  assert.deepEqual(ws.audit[before].outcome, { status: "refused", code: -32063 });
});

for (const [name, method, params, from, profiles] of [
  ["a gate refusal of an ordinary method", "whisper.ask", { task_id: "t", question: "?", options: ["a"] }, "human:a", CHAINED],
  ["a method that does not exist", "nothing.here", {}, "human:a", CHAINED],
  ["invalid parameters", "task.create", { kind: "k" }, "human:a", CHAINED],
  ["a call from a non-member", "control.pause", { task_id: "t", reason: "hold" }, "human:stranger", CHAINED],
  ["a refused read", "audit.verify_receipt", { receipt: {} }, "human:a", CHAINED],
] as const) {
  test(`${name} is not recorded`, () => {
    const { send, ws } = ready(profiles as unknown as string[]);
    const before = ws.audit.length;
    const r = send(method, params as Record<string, unknown>, from);
    assert.notEqual(r.error, undefined, "the call under test was not refused");
    assert.equal(ws.audit.length, before);
  });
}

test("a call whose signature fails is not recorded", () => {
  const { privateKey, jwk } = deriveKeypair("human:a");
  const c = new Coordinator({ requireSignatures: true, deterministicIds: true, deterministicClock: true,
                              defaultProfiles: ["core/1.0", "review/1.0", "security-signed/1.0"] } as never);
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
    params: { workspace: "w", from: "human:a", type: "human", jwks: { keys: [jwk] },
              profiles: ["core/1.0", "review/1.0", "security-signed/1.0"] } } as never);
  const ws = c.workspaces.get("w") as any;
  const before = ws.audit.length;
  const env: Record<string, unknown> = { jsonrpc: "2.0", id: "x", method: "task.create",
    params: { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "human:a" } };
  env.sig = signEnvelope(canonicalize(env as never), privateKey, jwk.kid);
  (env.params as Record<string, unknown>).kind = "altered after signing";
  const r = c.dispatch(env as never) as any;
  assert.equal(r.error.code, -32070);
  assert.equal(ws.audit.length, before);
});

test("the refused request is kept as it arrived, signature and all", () => {
  const { privateKey, jwk } = deriveKeypair("human:a");
  const profiles = ["core/1.0", "review/1.0", "security-signed/1.0", "audit-scitt/1.0"];
  const c = new Coordinator({ requireSignatures: true, deterministicIds: true, deterministicClock: true,
                              defaultProfiles: profiles } as never);
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
    params: { workspace: "w", from: "human:a", type: "human", jwks: { keys: [jwk] }, profiles } } as never);
  // control/1.0 is not advertised, so the gate refuses a privileged method.
  const env: Record<string, unknown> = { jsonrpc: "2.0", id: "x", method: "control.pause",
    params: { workspace: "w", from: "human:a", task_id: "tsk_absent", reason: "hold" } };
  env.sig = signEnvelope(canonicalize(env as never), privateKey, jwk.kid);
  const sent = JSON.parse(JSON.stringify(env));
  const r = c.dispatch(env as never) as any;
  assert.equal(r.error.code, -32601);
  const ws = c.workspaces.get("w") as any;
  const entry = ws.audit[ws.audit.length - 1];
  assert.deepEqual(entry.request, sent);
  const { sig, ...unsigned } = entry.request;
  assert.ok(verifyEnvelope(canonicalize(unsigned as never), sig, publicKeyFromJwk(jwk as never)));
});

test("a request that cannot be canonicalised is not recorded", () => {
  // The gate refuses before the ingress check, so this refusal reaches the
  // recording rule with a non-integer number in it. It cannot be hashed.
  const { send, ws } = ready(["core/1.0", "review/1.0", "audit-scitt/1.0"]);
  const before = ws.audit.length;
  const r = send("control.pause", { task_id: "t", reason: "hold", weight: 1.5 });
  assert.equal(r.error.code, -32601);
  assert.equal(ws.audit.length, before);
});

test("the privileged methods the gate can refuse agree with the catalogue", () => {
  // A gate refusal is recorded only for a privileged method. The catalogue's
  // `privileged` flag and the coordinators' list differ on methods the gate
  // never refuses, and they must agree on every method it can.
  const methods = JSON.parse(readFileSync(
    new URL("../../../schemas/profiles/chap-methods.schema.json", import.meta.url), "utf8",
  )).examples[0].methods as Record<string, { privileged?: boolean }>;
  for (const [method, owner] of Object.entries(OWNING_PROFILE)) {
    if (owner === "core/1.0" || ALWAYS_AVAILABLE.has(method)) continue;
    assert.equal(PRIVILEGED_METHODS.has(method), methods[method].privileged === true, method);
  }
});

// ------------------------------------------------------------ the chain

test("a chain with refusals on it verifies", () => {
  const { send } = ready();
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  send("decide.approve", { task_id: id, comment: "ok" }, "human:c");
  const v = send("audit.verify_chain");
  assert.equal(v.error, undefined, JSON.stringify(v.error));
  assert.equal(v.result.ok, true);
});

for (const [name, tamper] of [
  ["changing the refusal code", (e: any) => { e.outcome.code = -32602; }],
  ["stripping the outcome", (e: any) => { delete e.outcome; }],
  ["recasting the refusal as an accepted call", (e: any) => { e.envelope = e.request; delete e.request; delete e.outcome; }],
  ["altering the refused request", (e: any) => { e.request.params.comment = "altered"; }],
] as const) {
  test(`${name} breaks the chain`, () => {
    const { send, ws } = ready();
    const id = underReview(send);
    send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
    const refusal = ws.audit.find((e: any) => e.outcome !== undefined);
    assert.ok(refusal, "no refusal was recorded");
    (tamper as (e: unknown) => void)(refusal);
    assert.notEqual(send("audit.verify_chain").error, undefined);
  });
}

// ------------------------------------------------------------ readers

test("audit.read returns refusals under request, and outcome narrows", () => {
  const { send } = ready();
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");

  const all = send("audit.read").result.entries;
  const refused = send("audit.read", { filter: { outcome: "refused" } }).result.entries;
  const accepted = send("audit.read", { filter: { outcome: "accepted" } }).result.entries;
  assert.equal(refused.length, 1);
  assert.equal(accepted.length + refused.length, all.length);
  assert.deepEqual(Object.keys(refused[0]).sort(), ["arrived", "outcome", "prev_hash", "request", "seq"]);
  assert.ok(accepted.every((e: any) => e.envelope !== undefined && e.request === undefined));

  const byMethod = send("audit.read", { filter: { method: "decide.approve" } }).result.entries;
  assert.equal(byMethod.length, 1);
  assert.equal(byMethod[0].request.params.from, "human:a");
  const byTask = send("audit.read", { filter: { task_id: id, outcome: "refused" } }).result.entries;
  assert.equal(byTask.length, 1);
});

test("an unknown outcome filter is refused", () => {
  const { send } = ready();
  const r = send("audit.read", { filter: { outcome: "maybe" } });
  assert.equal(r.error.code, -32602);
  assert.equal(r.error.message, "filter.outcome must be 'accepted' or 'refused'");
});

test("a SCITT statement for a refusal carries what the link hashes", () => {
  const { send, ws } = ready();
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  const at = ws.audit.findIndex((e: any) => e.outcome !== undefined);
  assert.ok(at >= 0, "no refusal was recorded");
  const r = send("audit.submit_to_scitt");
  const e = ws.audit[at];
  assert.equal(r.result.statements[at].payload,
    canonicalize({ outcome: e.outcome, request: e.request } as never).toString("utf-8"));
});

test("listeners hear refusals", () => {
  const { c, send } = ready();
  const heard: any[] = [];
  c.onAudit((_ws, entry) => heard.push(entry));
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  const last = heard[heard.length - 1];
  assert.equal(last.request.method, "decide.approve");
  assert.deepEqual(last.outcome, { status: "refused", code: -32011 });
});

test("refusals survive a snapshot and a restore, and the chain still verifies", () => {
  const { c, send } = ready();
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  const snap = JSON.parse(JSON.stringify(c.snapshot()));

  const d = new Coordinator({ deterministicIds: true, deterministicClock: true,
                              defaultProfiles: CHAINED } as never);
  d.restore(snap);
  const before = (c.workspaces.get("w") as any).audit;
  const after = (d.workspaces.get("w") as any).audit;
  assert.deepEqual(after, before);
  const v = d.dispatch({ jsonrpc: "2.0", id: "v", method: "audit.verify_chain",
                         params: { workspace: "w", from: "human:a" } } as never) as any;
  assert.equal(v.error, undefined, JSON.stringify(v.error));
});
