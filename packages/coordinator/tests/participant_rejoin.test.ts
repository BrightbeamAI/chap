/**
 * Regression: participant.join must not replace an existing member. A re-join
 * keeps the member's role, scopes and keys, refreshes only the verified identity
 * binding, and never accepts self-asserted jwks for an already-admitted URI.
 * Guards the 0.2.9 fix.
 *
 * A verified token or presentation binds only to the participant it belongs
 * to: a re-join must carry the member's own subject or holder, or, for a member
 * with no verified subject, a token whose chap_participant_uri names it.
 * Otherwise anyone holding a token the verifier accepts could add a key to
 * another member and sign as that member. Mirrors test_participant_rejoin.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function ready(opts: Record<string, unknown> = {}) {
  const c = new Coordinator({ deterministicIds: true, ...opts });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "human:alice", type: "human",
    role: "reviewer", scopes: ["approve"], jwks: { keys: [{ kid: "alice-1", x: "A" }] } });
  return { c, s };
}

test("a re-join keeps role, scopes and key", () => {
  const { c, s } = ready();
  s("participant.join", { workspace: "w", from: "human:alice", type: "human",
    role: "owner", jwks: { keys: [{ kid: "attacker", x: "E" }] } });
  const m = (c.workspaces.get("w") as any).members.get("human:alice");
  assert.equal(m.role, "reviewer");
  assert.deepEqual(m.scopes, ["approve"]);
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["alice-1"]);
});

test("a re-join ignores self-asserted jwks", () => {
  const { c, s } = ready();
  s("participant.join", { workspace: "w", from: "human:alice", type: "human",
    jwks: { keys: [{ kid: "attacker", x: "E" }] } });
  const m = (c.workspaces.get("w") as any).members.get("human:alice");
  assert.ok(m.keys.every((k: any) => k.kid !== "attacker"));
});

test("a re-join refreshes the verified binding", () => {
  const claims: Record<string, unknown> = {
    good: { sub: "alice", auth_time: 111, chap_participant_uri: "human:alice" },
  };
  const c = new Coordinator({ deterministicIds: true, verifyOidcToken: (t: string) => (claims[t] as any) ?? null });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "human:alice", type: "human" });
  s("participant.join", { workspace: "w", from: "human:alice", type: "human", oidc_token: "good" });
  const m = (c.workspaces.get("w") as any).members.get("human:alice");
  assert.equal(m.oidc_sub, "alice");
  assert.equal(m.oidc_auth_time, 111);
});

test("a new member joining with jwks still registers keys", () => {
  const { c, s } = ready();
  s("participant.join", { workspace: "w", from: "agent:new", type: "agent",
    jwks: { keys: [{ kid: "n1", x: "N" }] } });
  const m = (c.workspaces.get("w") as any).members.get("agent:new");
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["n1"]);
});

// ------------------------------------------------ a token binds to its owner

const key = (kid: string) => ({ kty: "OKP", crv: "Ed25519", kid, x: kid.toUpperCase() });

const TOKENS: Record<string, Record<string, unknown>> = {
  "alice-1": { sub: "alice", auth_time: 111, cnf: { jwk: key("alice-k1") } },
  "alice-2": { sub: "alice", auth_time: 222, cnf: { jwk: key("alice-k2") } },
  "mallory": { sub: "mallory", auth_time: 333, cnf: { jwk: key("mallory-k") } },
  "mallory-naming-alice": { sub: "mallory", auth_time: 333,
    chap_participant_uri: "human:alice", cnf: { jwk: key("mallory-k") } },
  "naming-mallory": { sub: "x", auth_time: 1, chap_participant_uri: "human:mallory" },
  "alice-unnamed": { sub: "alice", auth_time: 444, cnf: { jwk: key("alice-k9") } },
};

function oidcReady() {
  const c = new Coordinator({ deterministicIds: true,
    verifyOidcToken: (t: string) => (TOKENS[t] as any) ?? null });
  const join = (uri: string, extra: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
      params: { workspace: "w", from: uri, type: "human", ...extra } } as never);
  c.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create", params: { workspace: "w" } } as never);
  return { c, join };
}

const memberOf = (c: Coordinator, uri = "human:alice"): any =>
  (c.workspaces.get("w") as any).members.get(uri);

test("a re-join with another subject's token is refused", () => {
  const { c, join } = oidcReady();
  assert.equal(join("human:alice", { role: "admin", oidc_token: "alice-1" }).error, undefined);
  const r = join("human:alice", { oidc_token: "mallory" });
  assert.equal(r.error.code, -32404);
  const m = memberOf(c);
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["alice-k1"]);
  assert.equal(m.oidc_sub, "alice");
  assert.equal(m.oidc_auth_time, 111);
});

test("a re-join with a token naming the member but another subject is refused", () => {
  const { c, join } = oidcReady();
  join("human:alice", { oidc_token: "alice-1" });
  const r = join("human:alice", { oidc_token: "mallory-naming-alice" });
  assert.equal(r.error.code, -32404);
  assert.deepEqual(memberOf(c).keys.map((k: any) => k.kid), ["alice-k1"]);
});

test("a re-join with the same subject adds a key and refreshes", () => {
  const { c, join } = oidcReady();
  join("human:alice", { oidc_token: "alice-1" });
  assert.equal(join("human:alice", { oidc_token: "alice-2" }).error, undefined);
  const m = memberOf(c);
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["alice-k1", "alice-k2"]);
  assert.equal(m.oidc_auth_time, 222);
});

test("binding an unbound member needs a token naming it", () => {
  const { c, join } = oidcReady();
  join("human:alice", { jwks: { keys: [key("alice-self")] } });
  const r = join("human:alice", { oidc_token: "alice-unnamed" });
  assert.equal(r.error.code, -32404);
  const m = memberOf(c);
  assert.equal(m.oidc_sub, undefined);
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["alice-self"]);
});

test("a token naming another participant is refused on first join", () => {
  const { c, join } = oidcReady();
  const r = join("human:alice", { oidc_token: "naming-mallory" });
  assert.equal(r.error.code, -32404);
  assert.equal((c.workspaces.get("w") as any).members.has("human:alice"), false);
});

const PRESENTATIONS: Record<string, Record<string, unknown>> = {
  "alice": { holder: "did:example:alice", cnf_jwk: key("alice-vp") },
  "alice-again": { holder: "did:example:alice", cnf_jwk: key("alice-vp2") },
  "mallory": { holder: "did:example:mallory", cnf_jwk: key("mallory-vp") },
};

function vcReady() {
  const c = new Coordinator({ deterministicIds: true,
    verifyVc: (vp: Record<string, unknown>) => (PRESENTATIONS[vp.who as string] as any) ?? null });
  const join = (uri: string, who?: string, extra: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
      params: { workspace: "w", from: uri, type: "human", ...extra,
        ...(who === undefined ? {} : { vc_presentation: { who } }) } } as never);
  c.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create", params: { workspace: "w" } } as never);
  return { c, join };
}

test("a re-join with another holder's presentation is refused", () => {
  const { c, join } = vcReady();
  join("human:alice", "alice");
  const r = join("human:alice", "mallory");
  assert.equal(r.error.code, -32411);
  const m = memberOf(c);
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["alice-vp"]);
  assert.equal(m.vc_holder, "did:example:alice");
});

test("a re-join with the same holder adds a key", () => {
  const { c, join } = vcReady();
  join("human:alice", "alice");
  assert.equal(join("human:alice", "alice-again").error, undefined);
  assert.deepEqual(memberOf(c).keys.map((k: any) => k.kid), ["alice-vp", "alice-vp2"]);
});

test("a presentation cannot bind an unbound member", () => {
  const { c, join } = vcReady();
  join("human:alice", undefined, { jwks: { keys: [key("alice-self")] } });
  const r = join("human:alice", "alice");
  assert.equal(r.error.code, -32411);
  const m = memberOf(c);
  assert.equal(m.vc_holder, undefined);
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["alice-self"]);
});

// ------------------------------------------------------------- edge cases

test("the four refusals carry their messages", () => {
  const { join } = oidcReady();
  assert.equal(join("human:alice", { oidc_token: "naming-mallory" }).error.message,
    "OIDC token is bound to another participant");
  join("human:alice", { oidc_token: "alice-1" });
  assert.equal(join("human:alice", { oidc_token: "mallory" }).error.message,
    "OIDC token subject does not match the member");
  join("human:bob", { jwks: { keys: [key("bob-self")] } });
  assert.equal(join("human:bob", { oidc_token: "alice-unnamed" }).error.message,
    "OIDC token does not name this member");
  const v = vcReady();
  v.join("human:alice", "alice");
  assert.equal(v.join("human:alice", "mallory").error.message,
    "Presentation holder does not match the member");
});

test("a good token with another holder's presentation changes nothing", () => {
  const tokens: Record<string, Record<string, unknown>> = {
    alice: { sub: "alice", auth_time: 5, cnf: { jwk: key("alice-k1") } },
    "alice-later": { sub: "alice", auth_time: 6, cnf: { jwk: key("alice-k2") } },
  };
  const c = new Coordinator({ deterministicIds: true,
    verifyOidcToken: (t: string) => (tokens[t] as any) ?? null,
    verifyVc: (vp: Record<string, unknown>) => (PRESENTATIONS[vp.who as string] as any) ?? null });
  const join = (extra: Record<string, unknown>): any =>
    c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
      params: { workspace: "w", from: "human:alice", type: "human", ...extra } } as never);
  join({ oidc_token: "alice", vc_presentation: { who: "alice" } });
  const r = join({ oidc_token: "alice-later", vc_presentation: { who: "mallory" } });
  assert.equal(r.error.code, -32411);
  const m = memberOf(c);
  assert.deepEqual(m.keys.map((k: any) => k.kid), ["alice-k1", "alice-vp"]);
  assert.equal(m.oidc_auth_time, 5);
});

test("a token with no string subject cannot re-join a bound member", () => {
  const tokens: Record<string, Record<string, unknown>> = {
    alice: { sub: "alice", auth_time: 1 }, "no-sub": { auth_time: 2 }, "number-sub": { sub: 7, auth_time: 2 },
  };
  const c = new Coordinator({ deterministicIds: true, verifyOidcToken: (t: string) => (tokens[t] as any) ?? null });
  const join = (token: string): any =>
    c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
      params: { workspace: "w", from: "human:alice", type: "human", oidc_token: token } } as never);
  join("alice");
  assert.equal(join("no-sub").error.code, -32404);
  assert.equal(join("number-sub").error.code, -32404);
  assert.equal(memberOf(c).oidc_sub, "alice");
});

test("a non-string participant URI claim is refused", () => {
  const c = new Coordinator({ deterministicIds: true,
    verifyOidcToken: () => ({ sub: "alice", chap_participant_uri: 42 }) as any });
  const r: any = c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human", oidc_token: "t" } } as never);
  assert.equal(r.error.code, -32404);
});

test("an empty holder falls back to the id and binds nothing else", () => {
  const presentations: Record<string, Record<string, unknown>> = {
    first: { holder: "", id: "did:example:alice" }, other: { holder: "", id: "did:example:mallory" },
  };
  const c = new Coordinator({ deterministicIds: true,
    verifyVc: (vp: Record<string, unknown>) => (presentations[vp.who as string] as any) ?? null });
  const join = (who: string): any =>
    c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
      params: { workspace: "w", from: "human:alice", type: "human", vc_presentation: { who } } } as never);
  join("first");
  assert.equal(memberOf(c).vc_holder, "did:example:alice");
  assert.equal(join("other").error.code, -32411);
});

test("a re-join token without acr clears the old acr", () => {
  const fresh = Math.floor(Date.now() / 1000);
  const tokens: Record<string, Record<string, unknown>> = {
    strong: { sub: "alice", auth_time: fresh, acr: "mfa" }, plain: { sub: "alice", auth_time: fresh },
  };
  const c = new Coordinator({ enforceStepUp: true, verifyOidcToken: (t: string) => (tokens[t] as any) ?? null });
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create", params: { workspace: "w", min_acr: "mfa" } } as never);
  const join = (token: string): any =>
    c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
      params: { workspace: "w", from: "human:alice", type: "human", role: "admin", oidc_token: token } } as never);
  const privileged = (): any =>
    c.dispatch({ jsonrpc: "2.0", id: "p", method: "workspace.set_profiles",
      params: { workspace: "w", from: "human:alice", profiles: ["core/1.0", "review/1.0"] } } as never);
  join("strong");
  assert.notEqual(privileged().error?.code, -32402);
  assert.equal(join("plain").error, undefined);
  assert.equal(memberOf(c).oidc_acr, undefined);
  assert.equal(privileged().error.code, -32402);
});
