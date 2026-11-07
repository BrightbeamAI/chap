/**
 * workspace.invite and the join policy (SPECIFICATION 6.3.3).
 *
 * workspace.invite was specified and not built. Nobody could be admitted by
 * an admin, a workspace could not limit who joins, and a member whose keys
 * were all revoked, or who held none where signatures are required, could
 * never sign again. An admin now invites: the invitation names the role the
 * member takes and may pin its keys and its OIDC subject; a workspace whose
 * join_policy is "invite" admits only invited participants and its first
 * member, its admin; and an invitation for a member that holds no key in
 * force registers new ones. Mirrors test_invitations.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Coordinator, type CoordinatorOptions } from "../src/coordinator.js";
import { canonicalize } from "../src/canonical.js";
import { deriveKeypair, signEnvelope } from "../src/crypto.js";
import { MemoryStore } from "../src/storage/store.js";
import type { Envelope } from "../src/types.js";

function ready(options: CoordinatorOptions = {}) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true, ...options });
  let n = 0;
  const env = (method: string, from: string, params: Record<string, unknown>): Envelope =>
    ({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from, ...params } });
  const call = (method: string, from: string, params: Record<string, unknown> = {}): any =>
    c.dispatch(env(method, from, params));
  /** A call signed with the key deriveKeypair gives `keyOf`, by default the sender's. */
  const signed = (method: string, from: string, params: Record<string, unknown> = {}, keyOf = from): any => {
    const e = env(method, from, params);
    const { privateKey, jwk } = deriveKeypair(keyOf);
    return c.dispatch({ ...e, sig: signEnvelope(canonicalize(e as never), privateKey, jwk.kid) });
  };
  const ws = () => c.workspaces.get("w")!;
  return { c, call, signed, ws };
}

const keyOf = (seed: string) => deriveKeypair(seed).jwk;

test("a workspace that admits by invitation takes its first member as its admin, and nobody uninvited after", () => {
  const { call, ws } = ready();
  call("workspace.create", "human:a", { join_policy: "invite" });
  assert.equal(call("workspace.describe", "human:a").result.join_policy, "invite");
  assert.deepEqual(call("participant.join", "agent:bot", { type: "agent" }).error,
    { code: -32011, message: "agent:bot has no invitation to this workspace" });
  assert.deepEqual(call("participant.join", "human:a", { type: "human", role: "reviewer" }).error,
    { code: -32602, message: "The first member of a workspace that admits by invitation is its admin" });
  assert.deepEqual(call("participant.join", "human:a", { type: "human" }).result, { joined: true, as: "human:a" });
  assert.equal(ws().members.get("human:a")!.role, "admin");
  assert.equal(call("participant.join", "human:b", { type: "human" }).error.code, -32011);
  assert.equal(ws().members.has("human:b"), false);
});

test("an invitation admits its invitee once, in the role it names", () => {
  const { call, ws } = ready({ joinPolicy: "invite" });
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin" });
  assert.deepEqual(call("workspace.invite", "human:a", { uri: "human:b", type: "human", role: "reviewer" }).result,
    { invited: true, uri: "human:b" });
  assert.equal(call("participant.join", "human:b", { type: "human", role: "drafter" }).error, undefined);
  assert.equal(ws().members.get("human:b")!.role, "reviewer");
  assert.equal(ws().invitations.size, 0);
  call("participant.leave", "human:b");
  assert.equal(call("participant.join", "human:b", { type: "human" }).error.code, -32011);
});

test("after the first member, only an invitation grants the admin role", () => {
  const { call, ws } = ready();
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin" });
  call("workspace.invite", "human:a", { uri: "human:c", type: "human", role: "admin" });
  call("workspace.invite", "human:a", { uri: "human:d", type: "human", role: "reviewer" });
  assert.equal(call("participant.join", "human:c", { type: "human", role: "admin" }).error, undefined);
  assert.equal(ws().members.get("human:c")!.role, "admin");
  assert.deepEqual(call("participant.join", "human:d", { type: "human", role: "admin" }).error,
    { code: -32011, message: "The admin role is granted by an admin" });
  // An invitation that names no role leaves the join's own, admin excepted.
  call("workspace.invite", "human:c", { uri: "human:e", type: "human" });
  assert.equal(call("participant.join", "human:e", { type: "human", role: "admin" }).error.code, -32011);
  assert.equal(call("participant.join", "human:e", { type: "human", role: "drafter" }).error, undefined);
  assert.equal(ws().members.get("human:e")!.role, "drafter");
});

test("only an admin invites, and an invitation's names are checked as a join's are", () => {
  const { c, call, ws } = ready();
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin" });
  call("participant.join", "agent:b", { type: "agent" });
  const invite = (from: string, params: Record<string, unknown>) => call("workspace.invite", from, params).error;
  assert.deepEqual(invite("agent:b", { uri: "human:x", type: "human" }),
    { code: -32011, message: "workspace.invite requires the admin role" });
  assert.equal(invite("human:ghost", { uri: "human:x", type: "human" }).code, -32011);
  assert.deepEqual(invite("human:a", { uri: "human:x", type: "agent" }),
    { code: -32602, message: 'uri must start with "agent:" for type agent' });
  assert.deepEqual(invite("human:a", { uri: "agent:x", type: "agent", role: "admin" }),
    { code: -32602, message: "The admin role is held by a human or a service" });
  assert.deepEqual(invite("human:a", { uri: "human:x", type: "human", jwks: { keys: [{ kty: "OKP" }] } }),
    { code: -32602, message: "jwks names no key: each key needs a kid" });
  assert.deepEqual(invite("human:a", { uri: "human:x", type: "human", oidc_sub: "s" }),
    { code: -32602, message: "oidc_sub names a token subject, and this Coordinator has no token verifier" });
  assert.equal(invite("human:a", { type: "human" }).message, "Missing field: uri");
  assert.equal(ws().invitations.size, 0);
  // A member's refused attempt is on the log; the admin's accepted one is too.
  const before = ws().audit.length;
  invite("agent:b", { uri: "human:x", type: "human" });
  call("workspace.invite", "human:a", { uri: "human:x", type: "human" });
  assert.deepEqual(ws().audit.slice(before).map((e) => e.outcome?.code ?? "accepted"), [-32011, "accepted"]);
  assert.equal(c.workspaces.get("w")!.invitations.get("human:x")!.invited_by, "human:a");
});

test("an invitation that pins keys admits only those keys, and the member holds them", () => {
  const { call, ws } = ready();
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin" });
  const pinned = keyOf("human:e");
  call("workspace.invite", "human:a", { uri: "human:e", type: "human", jwks: { keys: [pinned] } });
  const other = keyOf("human:e#mallory");
  assert.deepEqual(call("participant.join", "human:e", { type: "human", jwks: { keys: [other] } }).error,
    { code: -32011, message: "human:e joins with keys its invitation does not name", data: { kids: [other.kid] } });
  // The pinned kid on other key material is another key.
  const forged = { ...other, kid: pinned.kid };
  assert.equal(call("participant.join", "human:e", { type: "human", jwks: { keys: [forged] } }).error.code, -32011);
  assert.equal(ws().members.has("human:e"), false);
  assert.equal(call("participant.join", "human:e", { type: "human" }).error, undefined);
  assert.deepEqual(ws().members.get("human:e")!.keys.map((k) => k.jwk), [pinned]);
});

test("an invitation that names an OIDC subject admits a token for that subject", () => {
  const verifyOidcToken = (t: string) => (t.startsWith("tok:") ? JSON.parse(t.slice(4)) : null);
  const { call, ws } = ready({ verifyOidcToken });
  call("workspace.create", "service:op");
  call("participant.join", "service:op", { type: "service", role: "admin" });
  call("workspace.invite", "service:op", { uri: "human:f", type: "human", oidc_sub: "sub-f" });
  assert.equal(call("participant.join", "human:f", { type: "human" }).error.code, -32403);
  assert.deepEqual(call("participant.join", "human:f",
    { type: "human", oidc_token: `tok:${JSON.stringify({ sub: "sub-g" })}` }).error,
    { code: -32404, message: "OIDC token subject does not match the invitation" });
  assert.equal(call("participant.join", "human:f",
    { type: "human", oidc_token: `tok:${JSON.stringify({ sub: "sub-f" })}` }).error, undefined);
  assert.equal(ws().members.get("human:f")!.oidc_sub, "sub-f");
});

test("an invitation for a member that holds no key in force registers new keys", () => {
  // A signed workspace: the admin's calls are signed, and so are a person's.
  const { call, signed, ws } = ready({ requireSignatures: true });
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin", jwks: { keys: [keyOf("human:a")] } });
  call("participant.join", "human:c", { type: "human", jwks: { keys: [keyOf("human:c")] } });
  const fresh = keyOf("human:c#2");
  const invite = { uri: "human:c", type: "human", jwks: { keys: [fresh] } };
  assert.deepEqual(signed("workspace.invite", "human:a", invite).error, { code: -32602,
    message: "human:c is a member and holds a key in force; its keys change through participant.rotate_key" });
  assert.equal(signed("participant.revoke_key", "human:a",
    { target_uri: "human:c", kid: keyOf("human:c").kid }).error, undefined);
  assert.equal(signed("workspace.describe", "human:c").error.code, -32072);
  // The admin signs the invitation like any call in a signed workspace.
  assert.equal(call("workspace.invite", "human:a", invite).error.code, -32070);
  assert.deepEqual(signed("workspace.invite", "human:a",
    { uri: "human:c", type: "human", jwks: { keys: [keyOf("human:c")] } }).error,
    { code: -32602, message: `Key ${keyOf("human:c").kid} was human:c's; a new key takes a new kid` });
  assert.deepEqual(signed("workspace.invite", "human:a", { ...invite, role: "admin" }).error.code, -32602);
  assert.deepEqual(signed("workspace.invite", "human:a", invite).result,
    { invited: true, uri: "human:c", registered: [fresh.kid] });
  assert.equal(signed("workspace.describe", "human:c", {}, "human:c#2").error, undefined);
  assert.equal(ws().invitations.size, 0);
});

test("where a token verifier is configured, an invitation does not register a person's keys", () => {
  // The admin could otherwise revoke a person's keys, invite them with a key
  // the admin holds, and sign as them. The person joins again with a token
  // that binds a new key.
  const verifyOidcToken = (t: string) => (t.startsWith("tok:") ? JSON.parse(t.slice(4)) : null);
  const { call, ws } = ready({ verifyOidcToken });
  call("workspace.create", "human:a");
  const token = (sub: string, uri: string) => `tok:${JSON.stringify({ sub, chap_participant_uri: uri })}`;
  assert.equal(call("participant.join", "human:a", { type: "human", role: "admin",
    oidc_token: token("sub-a", "human:a") }).error, undefined);
  assert.equal(call("participant.join", "human:c", { type: "human",
    oidc_token: token("sub-c", "human:c") }).error, undefined);
  assert.deepEqual(call("workspace.invite", "human:a",
    { uri: "human:c", type: "human", jwks: { keys: [keyOf("human:a#2")] } }).error,
    { code: -32602, message: "human:c registers a new key by joining again with a token that binds it; "
      + "an invitation does not register a person's keys here" });
  assert.deepEqual(ws().members.get("human:c")!.keys, []);
});

test("where a verifier is configured, an invitation pins no keys for a person, a member or not", () => {
  // A join whose token or presentation binds no key would take up keys the
  // admin pinned, and the admin could sign as the person; under a credential
  // verifier as under a token verifier.
  const token = (sub: string, uri: string) => `tok:${JSON.stringify({ sub, chap_participant_uri: uri })}`;
  const oidc = ready({ verifyOidcToken: (t: string) => (t.startsWith("tok:") ? JSON.parse(t.slice(4)) : null) });
  oidc.call("workspace.create", "human:a");
  oidc.call("participant.join", "human:a", { type: "human", role: "admin", oidc_token: token("sub-a", "human:a") });
  assert.deepEqual(oidc.call("workspace.invite", "human:a",
    { uri: "human:q", type: "human", jwks: { keys: [keyOf("human:a#2")] } }).error,
    { code: -32602, message: "human:q joins with a token that binds its key; an invitation does not register a person's keys here" });
  assert.equal(oidc.ws().invitations.has("human:q"), false);
  assert.deepEqual(oidc.call("workspace.invite", "human:a", { uri: "human:q", type: "human" }).result,
    { invited: true, uri: "human:q" });

  const vc = ready({ verifyVc: (vp: Record<string, unknown>) => (typeof vp.holder === "string" ? { holder: vp.holder } : null) });
  vc.call("workspace.create", "human:a");
  vc.call("participant.join", "human:a", { type: "human", role: "admin", vc_presentation: { holder: "did:a" } });
  assert.equal(vc.call("participant.join", "human:p", { type: "human", vc_presentation: { holder: "did:p" } }).error, undefined);
  assert.deepEqual(vc.call("workspace.invite", "human:a",
    { uri: "human:p", type: "human", jwks: { keys: [keyOf("human:a#2")] } }).error,
    { code: -32602, message: "human:p registers a new key by joining again with a token that binds it; "
      + "an invitation does not register a person's keys here" });
  assert.deepEqual(vc.ws().members.get("human:p")!.keys, []);
  assert.equal(vc.call("workspace.invite", "human:a",
    { uri: "human:q", type: "human", jwks: { keys: [keyOf("human:a#2")] } }).error.code, -32602);
});

test("an invitation and the join policy are kept by the store, and an invitation used up leaves it", () => {
  const store = new MemoryStore();
  const first = ready({ store, joinPolicy: "invite" });
  first.call("workspace.create", "human:a");
  first.call("participant.join", "human:a", { type: "human" });
  first.call("workspace.invite", "human:a", { uri: "human:b", type: "human", role: "reviewer",
    jwks: { keys: [keyOf("human:b")] } });
  const second = new Coordinator({ store, deterministicIds: true });
  const ws = second.workspaces.get("w")!;
  assert.equal(ws.join_policy, "invite");
  assert.deepEqual(ws.invitations.get("human:b"), first.ws().invitations.get("human:b"));
  const joined = second.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
    params: { workspace: "w", from: "human:b", type: "human" } }) as any;
  assert.equal(joined.error, undefined);
  const third = new Coordinator({ store });
  assert.equal(third.workspaces.get("w")!.invitations.size, 0);
  assert.equal(third.workspaces.get("w")!.members.get("human:b")!.role, "reviewer");
});

test("a workspace written before the join policy existed admits anyone", () => {
  const store = new MemoryStore();
  const { call } = ready({ store });
  call("workspace.create", "human:a");
  const record = store.loadOne("w")!;
  const data = record.data as Record<string, unknown>;
  delete data.join_policy;
  delete data.invitations;
  const old = new MemoryStore();
  old.save({ ...record, data });
  const c = new Coordinator({ store: old });
  assert.equal(c.workspaces.get("w")!.join_policy, "open");
  assert.equal(c.workspaces.get("w")!.invitations.size, 0);
});

test("join_policy is open or invite", () => {
  const { call } = ready();
  assert.deepEqual(call("workspace.create", "human:a", { join_policy: "closed" }).error,
    { code: -32602, message: "join_policy must be open or invite" });
});

test("the SQLite view lists invitations, and a view written before them is replaced", async () => {
  let SqliteStore: typeof import("../src/storage/sqlite.js").SqliteStore;
  try {
    ({ SqliteStore } = await import("../src/storage/sqlite.js"));
    new SqliteStore(":memory:").close();
  } catch {
    return;
  }
  const dir = mkdtempSync(join(tmpdir(), "chap-invite-"));
  try {
    const path = join(dir, "chap.db");
    const store = new SqliteStore(path);
    const view = () => (store.db.prepare("SELECT sql FROM sqlite_master WHERE name = 'chap_workspaces'")
      .get() as { sql: string }).sql;
    const current = view();
    assert.match(current, /'\$\.invitations'/);
    store.db.exec("DROP VIEW chap_workspaces");
    store.db.exec(current.replace(", '$.invitations', json((SELECT json_group_object(id, json(data)) " +
      "FROM chap_objects WHERE workspace = m.workspace AND collection = 'invitations'))", ""));
    assert.doesNotMatch(view(), /invitations/);
    store.close();
    const reopened = new SqliteStore(path);
    const sql = (reopened.db.prepare("SELECT sql FROM sqlite_master WHERE name = 'chap_workspaces'").get() as { sql: string }).sql;
    assert.equal(sql, current);
    reopened.close();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
