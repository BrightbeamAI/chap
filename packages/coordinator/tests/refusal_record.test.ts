/**
 * SPECIFICATION 10.1: a refused call that is a governed attempt is recorded.
 *
 * The attempt sits on the log under `request`, with an `outcome` beside it,
 * so a reader keyed on `envelope` passes it by instead of replaying it. The
 * chain link hashes the outcome together with the request, so altering either
 * half breaks the chain, and moving the record under `envelope` fails the
 * shape check. What happens to a signed request sent again is in
 * refusal_signed.test.ts.
 *
 * The mirror of this file is
 * packages/coordinator-py/tests/test_refusal_record.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Coordinator, PRIVILEGED_METHODS } from "../src/coordinator.ts";
import { ALWAYS_AVAILABLE, OWNING_PROFILE } from "../src/catalogue.ts";
import { canonicalize, ZERO_HASH } from "../src/canonical.ts";
import { entryRecord, linkHash } from "../src/audit.ts";
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

test("a request that cannot be canonicalised is refused before the gate, and not recorded", () => {
  // The request itself is checked first (SPECIFICATION 10.1), so a
  // non-integer number is refused -32602 before the gate could refuse the
  // privileged method, which would be recorded.
  const { send, ws } = ready(["core/1.0", "review/1.0", "audit-scitt/1.0"]);
  const before = ws.audit.length;
  const r = send("control.pause", { task_id: "t", reason: "hold", weight: 1.5 });
  assert.equal(r.error.code, -32602);
  assert.equal(ws.audit.length, before);
});

test("a method that does not exist is refused before the pause, and not recorded", () => {
  const { send, ws } = ready();
  send("control.pause", { scope: "workspace", reason: "incident" });
  const before = ws.audit.length;
  assert.equal(send("nothing.here").error.code, -32601);
  assert.equal(ws.audit.length, before);
});

test("an empty or non-string method is an invalid request", () => {
  const { c, ws } = ready();
  const before = ws.audit.length;
  for (const method of ["", 5]) {
    const r = c.dispatch({ jsonrpc: "2.0", id: "m", method, params: { workspace: "w", from: "human:a" } } as never) as any;
    assert.equal(r.error.code, -32600, JSON.stringify(method));
  }
  assert.equal(ws.audit.length, before);
});

test("an oversized request from a member is not recorded", () => {
  const { send, ws } = ready(CHAINED, { maxEnvelopeBytes: 400 });
  const id = underReview(send);
  const before = ws.audit.length;
  const r = send("decide.approve", { task_id: id, comment: "x".repeat(500) }, "human:a");
  assert.equal(r.error.code, -32600);
  assert.equal(ws.audit.length, before);
});

test("a fault in the Coordinator is not recorded", () => {
  const { c, send, ws } = ready();
  c.handlers.set("task.create", () => { throw new Error("boom"); });
  const before = ws.audit.length;
  assert.equal(send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").error.code, -32603);
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

// ------------------------------------------------------------ unsigned calls sent again

test("an unsigned request identical to a recorded refusal is evaluated again", () => {
  // Without a signature anyone can send any call in any name, so there is
  // nothing for the resubmission rule to protect (refusal_signed.test.ts).
  const { c, send, ws } = ready();
  const id = underReview(send);
  const env = { jsonrpc: "2.0", id: "late", method: "decide.approve",
                params: { workspace: "w", from: "human:a", task_id: id, comment: "ok" } };
  assert.equal((c.dispatch(JSON.parse(JSON.stringify(env)) as never) as any).error.code, -32011);
  send("review.request", { task_id: id, artefact: { body: "draft" }, to: "human:a" }, "agent:b");
  const again = c.dispatch(JSON.parse(JSON.stringify(env)) as never) as any;
  assert.equal(again.error, undefined, JSON.stringify(again.error));
  assert.equal(ws.tasks.get(id).state, "completed");
});

test("an unsigned refusal is recorded each time it is refused", () => {
  const { c, send, ws } = ready();
  const id = underReview(send);
  const env = { jsonrpc: "2.0", id: "late", method: "decide.approve",
                params: { workspace: "w", from: "human:a", task_id: id, comment: "ok" } };
  const before = ws.audit.length;
  for (let i = 0; i < 2; i++) {
    assert.equal((c.dispatch(JSON.parse(JSON.stringify(env)) as never) as any).error.code, -32011);
  }
  assert.equal(ws.audit.length, before + 2);
});

test("the recorded request is a copy of the one sent", () => {
  const { c, send, ws } = ready();
  const id = underReview(send);
  const env = { jsonrpc: "2.0", id: "late", method: "decide.approve",
                params: { workspace: "w", from: "human:a", task_id: id, comment: "ok" } };
  c.dispatch(env as never);
  env.params.comment = "changed by the caller afterwards";
  assert.equal(ws.audit[ws.audit.length - 1].request.params.comment, "ok");
});

test("a SCITT submitter receives what the link hashes for a refusal", () => {
  const statements: any[] = [];
  const { send, ws } = ready(CHAINED, { scittSubmitter: (s: unknown) => { statements.push(s); return { ok: true }; } });
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  const at = ws.audit.findIndex((e: any) => e.outcome !== undefined);
  assert.equal(send("audit.submit_to_scitt").error, undefined);
  const e = ws.audit[at];
  assert.equal(statements[at].payload,
    canonicalize({ outcome: e.outcome, request: e.request } as never).toString("utf-8"));
});

// ------------------------------------------------------------ what stays off

test("a refused join is not recorded, even from a member's URI", () => {
  // participant.join is exempt from signature checks, so its refusal proves
  // nothing about who sent it.
  const c = new Coordinator({ requireSignatures: true, deterministicIds: true, deterministicClock: true,
                              verifyOidcToken: () => null,
                              defaultProfiles: ["core/1.0", "review/1.0"] } as never);
  const { jwk } = deriveKeypair("human:a");
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
    params: { workspace: "w", from: "human:a", type: "human", jwks: { keys: [jwk] } } } as never);
  const ws = c.workspaces.get("w") as any;
  const before = ws.audit.length;
  const r = c.dispatch({ jsonrpc: "2.0", id: "forged", method: "participant.join",
    params: { workspace: "w", from: "human:a", type: "human", oidc_token: "forged" } } as never) as any;
  assert.notEqual(r.error, undefined);
  assert.equal(ws.audit.length, before);
});

test("a method the catalogue lists but no coordinator implements is not recorded", () => {
  const { send, ws } = ready();
  const before = ws.audit.length;
  assert.equal(send("workspace.invite", { invitee: "human:z" }).error.code, -32601);
  assert.equal(ws.audit.length, before);
});

test("a refused audit.submit_to_scitt is not recorded", () => {
  const { send, ws } = ready();
  send("control.pause", { scope: "workspace", reason: "incident" });
  const before = ws.audit.length;
  assert.notEqual(send("audit.submit_to_scitt").error, undefined);
  assert.equal(ws.audit.length, before);
});

test("a refusal whose code is not an integer is not recorded", () => {
  const { c, send, ws } = ready();
  c.handlers.set("custom.broken", () => ({ error: { code: "bad" as never, message: "no" } }));
  const before = ws.audit.length;
  send("custom.broken");
  assert.equal(ws.audit.length, before);
});

test("a step-up refusal is recorded", () => {
  const { send, ws } = ready(CHAINED, { enforceStepUp: true });
  const before = ws.audit.length;
  const r = send("control.pause", { scope: "workspace", reason: "incident" });
  assert.equal(r.error.code, -32402);
  assert.equal(ws.audit.length, before + 1);
  assert.deepEqual(ws.audit[before].outcome, { status: "refused", code: -32402 });
});

test("a refused notification is recorded without an id", () => {
  const { c, send, ws } = ready();
  const id = underReview(send);
  const before = ws.audit.length;
  c.dispatch({ jsonrpc: "2.0", method: "decide.approve",
               params: { workspace: "w", from: "human:a", task_id: id, comment: "ok" } } as never);
  assert.equal(ws.audit.length, before + 1);
  assert.equal("id" in ws.audit[before].request, false);
});

// ------------------------------------------------------------ entry shape

for (const [name, tamper] of [
  ["moving the refusal's record under envelope",
    (e: any) => { e.envelope = { outcome: e.outcome, request: e.request }; delete e.request; delete e.outcome; }],
  ["holding both an envelope and a request", (e: any) => { e.envelope = e.request; }],
  ["an outcome of another status", (e: any) => { e.outcome.status = "accepted"; }],
  ["a code that is not an integer", (e: any) => { e.outcome.code = "-32011"; }],
  ["a null outcome", (e: any) => { e.outcome = null; }],
] as const) {
  test(`${name} breaks the chain`, () => {
    const { send, ws } = ready();
    const id = underReview(send);
    send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
    const refusal = ws.audit.find((e: any) => e.outcome !== undefined);
    (tamper as (e: unknown) => void)(refusal);
    assert.notEqual(send("audit.verify_chain").error, undefined);
  });
}

test("adding an outcome to an accepted entry breaks the chain", () => {
  const { send, ws } = ready();
  ws.audit[ws.audit.length - 1].outcome = { status: "refused", code: -32011 };
  assert.notEqual(send("audit.verify_chain").error, undefined);
});

/** Recompute every link after a tamper, so only the shape check can catch it. */
function relink(ws: any): void {
  let prev = ZERO_HASH;
  for (const e of ws.audit) {
    e.prev_hash = prev;
    prev = linkHash(entryRecord(e), prev);
  }
  ws.chain_head = prev;
}

for (const [name, tamper] of [
  ["an outcome of another status", (e: any) => { e.outcome = { ...e.outcome, status: "accepted" }; }],
  ["a code that is not an integer", (e: any) => { e.outcome = { ...e.outcome, code: "-32011" }; }],
  ["a request that is not a JSON-RPC call", (e: any) => { const { jsonrpc: _j, ...rest } = e.request; e.request = rest; }],
  ["a request whose method is not a string", (e: any) => { e.request = { ...e.request, method: 5 }; }],
  ["a request left beside an accepted envelope", (e: any) => { e.envelope = e.request; delete e.outcome; }],
] as const) {
  test(`${name} is reported malformed even with every link recomputed`, () => {
    const { send, ws } = ready();
    const id = underReview(send);
    send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
    const refusal = ws.audit.find((e: any) => e.outcome !== undefined);
    (tamper as (e: unknown) => void)(refusal);
    relink(ws);
    const r = send("audit.verify_chain");
    assert.match(r.error?.message ?? "", new RegExp(`seq ${refusal.seq}: malformed entry`));
  });
}

test("the report names a malformed entry alone, and not the entries after it", () => {
  const { send, ws } = ready();
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  send("decide.approve", { task_id: id, comment: "ok" }, "human:c");
  const refusal = ws.audit.find((e: any) => e.outcome !== undefined);
  refusal.envelope = { outcome: refusal.outcome, request: refusal.request };
  delete refusal.request; delete refusal.outcome;
  const r = send("audit.verify_chain");
  assert.equal(r.error.message, `seq ${refusal.seq}: malformed entry`);
});

// ------------------------------------------------------------ filters

test("a null outcome filter reads as no filter", () => {
  const { send } = ready();
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  const all = send("audit.read").result.entries;
  const r = send("audit.read", { filter: { outcome: null } });
  assert.equal(r.error, undefined, JSON.stringify(r.error));
  assert.equal(r.result.entries.length, all.length);
});

test("a filter that is not an object reads as no filter", () => {
  const { send } = ready();
  const all = send("audit.read").result.entries;
  assert.equal(send("audit.read", { filter: "x" }).result.entries.length, all.length);
});

test("from matches a refused call's sender", () => {
  const { send } = ready();
  const id = underReview(send);
  send("decide.approve", { task_id: id, comment: "ok" }, "human:a");
  const mine = send("audit.read", { filter: { from: "human:a", outcome: "refused" } }).result.entries;
  assert.equal(mine.length, 1);
});

test("a refused whisper.answer with a malformed whisper_id leaves task filters working", () => {
  const profiles = [...CHAINED, "whisper/1.0"];
  const { send } = ready(profiles);
  send("control.pause", { scope: "workspace", reason: "incident" });
  const r = send("whisper.answer", { whisper_id: { x: 1 }, answer_option: "a" }, "human:c");
  assert.equal(r.error.code, -32063);
  const read = send("audit.read", { filter: { task_id: "tsk_absent" } });
  assert.equal(read.error, undefined, JSON.stringify(read.error));
});

test("an answer option that is an object is refused as outside the set, and recorded", () => {
  const profiles = [...CHAINED, "whisper/1.0"];
  const { send, ws } = ready(profiles);
  const tid = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").result.task_id;
  const wid = send("whisper.ask", { to: "human:c", task_id: tid, question: "?", deadline_ms: 60000,
                                   default_if_lapsed: "a", options: [{ id: "a" }] }, "agent:b").result.whisper_id;
  const before = ws.audit.length;
  const r = send("whisper.answer", { whisper_id: wid, answer_option: { x: 1 } }, "human:c");
  assert.equal(r.error.code, -32022);
  assert.equal(r.error.message, 'Answer option {"x":1} not in option set');
  assert.equal(ws.audit.length, before + 1);
});

test("an option id that is an object never matches, as both coordinators compare ids", () => {
  const profiles = [...CHAINED, "whisper/1.0"];
  const { send, ws } = ready(profiles);
  const tid = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").result.task_id;
  const wid = send("whisper.ask", { to: "human:c", task_id: tid, question: "?", deadline_ms: 60000,
                                   default_if_lapsed: "a", options: [{ id: { k: 1 } }, { id: 1 }] },
                   "agent:b").result.whisper_id;
  const before = ws.audit.length;
  assert.equal(send("whisper.answer", { whisper_id: wid, answer_option: { k: 1 } }, "human:c").error.code, -32022);
  assert.equal(send("whisper.answer", { whisper_id: wid, answer_option: true }, "human:c").error.code, -32022);
  assert.equal(ws.audit.length, before + 2);
});

test("a null answer option is no option, and its refusal is not recorded", () => {
  const profiles = [...CHAINED, "whisper/1.0"];
  const { send, ws } = ready(profiles);
  const tid = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").result.task_id;
  const wid = send("whisper.ask", { to: "human:c", task_id: tid, question: "?", deadline_ms: 60000,
                                   default_if_lapsed: "a", options: [{ id: "a" }] }, "agent:b").result.whisper_id;
  const before = ws.audit.length;
  assert.equal(send("whisper.answer", { whisper_id: wid, answer_option: null }, "human:c").error.code, -32602);
  assert.equal(ws.audit.length, before);
});

/** An extra parameter nested so that the whole envelope is `depth` levels deep. */
function nestedTo(depth: number): unknown {
  let v: unknown = 1;
  for (let i = 0; i < depth - 3; i++) v = { x: v };
  return v;
}

test("a request nested deeper than the limit is an invalid request, and not recorded", () => {
  const { send, ws } = ready();
  const id = underReview(send);
  const before = ws.audit.length;
  assert.equal(send("decide.approve", { task_id: id, deep: nestedTo(64) }, "human:a").error.code, -32011);
  assert.equal(ws.audit.length, before + 1);
  assert.equal(send("decide.approve", { task_id: id, deep: nestedTo(65) }, "human:a").error.code, -32600);
  assert.equal(ws.audit.length, before + 1);
});

test("an empty answer is no answer, in both references", () => {
  const profiles = [...CHAINED, "whisper/1.0"];
  const { send, ws } = ready(profiles);
  const tid = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").result.task_id;
  const wid = send("whisper.ask", { to: "human:c", task_id: tid, question: "?", deadline_ms: 60000,
                                   default_if_lapsed: "a" }, "agent:b").result.whisper_id;
  const before = ws.audit.length;
  assert.equal(send("whisper.answer", { whisper_id: wid, answer: "" }, "human:c").error.code, -32602);
  assert.equal(send("whisper.answer", { whisper_id: wid, answer_option: null }, "human:c").error.code, -32602);
  assert.equal(ws.audit.length, before);
  assert.equal(send("whisper.answer", { whisper_id: wid, answer: "yes" }, "human:c").error, undefined);
});

test("whisper options must be a list", () => {
  const profiles = [...CHAINED, "whisper/1.0"];
  const { send } = ready(profiles);
  const tid = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").result.task_id;
  const r = send("whisper.ask", { to: "human:c", task_id: tid, question: "?", deadline_ms: 60000,
                                 default_if_lapsed: "a", options: "abc" }, "agent:b");
  assert.equal(r.error.code, -32602);
});

test("an option id matches an equal number, and the refusal shows the option canonically", () => {
  const profiles = [...CHAINED, "whisper/1.0"];
  const { c, send } = ready(profiles);
  const tid = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }, "agent:b").result.task_id;
  const ask = (options: unknown) => send("whisper.ask", { to: "human:c", task_id: tid, question: "?",
    deadline_ms: 60000, default_if_lapsed: 1, options }, "agent:b").result.whisper_id;
  const refused = send("whisper.answer", { whisper_id: ask([{ id: "a" }]), answer_option: { b: 1, "2": 0 } }, "human:c");
  assert.equal(refused.error.message, 'Answer option {"2":0,"b":1} not in option set');
  // JSON 1.0 is the number 1, which is the option's id.
  const wid = ask([{ id: 1 }]);
  const r = c.dispatch(JSON.parse(`{"jsonrpc":"2.0","id":"n","method":"whisper.answer",
    "params":{"workspace":"w","from":"human:c","whisper_id":"${wid}","answer_option":1.0}}`)) as any;
  assert.equal(r.error, undefined, JSON.stringify(r.error));
});
