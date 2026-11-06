/**
 * participant.join: types and URIs, identity required of people, credentials
 * kept off the chain, and a key bound through the token's nonce
 * (6 November review, items 4 and 5).
 *
 * A join took any type with any URI, so "human:mallory" could join as an
 * agent. Where a token verifier was configured a person could join with no
 * token at all, and a join's token or presentation was written to the log,
 * where audit.read handed it to any reader. Mirrors test_joins.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { Coordinator, type CoordinatorOptions } from "../src/coordinator.js";
import { canonicalize } from "../src/canonical.js";
import { jwkThumbprint } from "../src/crypto.js";

const RFC8037_KEY = { kty: "OKP", crv: "Ed25519", x: "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo" };
const key = (kid: string, x = kid.toUpperCase()) => ({ kty: "OKP", crv: "Ed25519", kid, x });
const ecKey = (kid: string) => ({ kty: "EC", crv: "P-256", kid,
  x: "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU", y: "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0" });

function coordinator(options: Partial<CoordinatorOptions> = {}) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true, ...options });
  let n = 0;
  const join = (from: string, type: string, extra: Record<string, unknown> = {}, workspace = "w"): any =>
    c.dispatch({ jsonrpc: "2.0", id: `j${++n}`, method: "participant.join",
                 params: { workspace, from, type, ...extra } });
  return { c, join };
}

const keysOf = (c: Coordinator, uri: string) =>
  c.workspaces.get("w")!.members.get(uri)!.keys.map((k) => k.kid);

// ---------------------------------------------------------------- types and URIs

test("a participant is one of five types, and its URI's scheme names the type", () => {
  const { c, join } = coordinator();
  for (const type of ["human", "agent", "service", "group", "workspace"]) {
    assert.deepEqual(join(`${type}:x`, type).result, { joined: true, as: `${type}:x` });
  }
  assert.deepEqual(join("bot:x", "bot").error,
    { code: -32602, message: "type must be human, agent, service, group or workspace" });
  assert.deepEqual(join("human:mallory", "agent").error,
    { code: -32602, message: 'from must start with "agent:" for type agent' });
  assert.deepEqual(join("alice", "human").error,
    { code: -32602, message: 'from must start with "human:" for type human' });
  assert.equal(c.workspaces.get("w")!.members.size, 5);
});

test("a credential of the wrong type is refused", () => {
  const { join } = coordinator();
  assert.deepEqual(join("human:a", "human", { oidc_token: { t: 1 } }).error,
    { code: -32602, message: "oidc_token must be a string" });
  assert.deepEqual(join("human:a", "human", { vc_presentation: "vp" }).error,
    { code: -32602, message: "vc_presentation must be an object" });
});

// ------------------------------------------------------- identity required of people

const TOKENS: Record<string, Record<string, unknown>> = {
  alice: { sub: "alice", cnf: { jwk: key("alice-k1") } },
  bob: { sub: "bob" },
};
const PRESENTATIONS: Record<string, Record<string, unknown>> = {
  carol: { holder: "did:example:carol", cnf_jwk: key("carol-vp") },
};
const verifyOidcToken = (t: string) => (TOKENS[t] as never) ?? null;
const verifyVc = (vp: Record<string, unknown>) => (PRESENTATIONS[vp.who as string] as never) ?? null;

test("where a token verifier is configured and advertised, a person joins with a token", () => {
  const { c, join } = coordinator({ verifyOidcToken });
  assert.deepEqual(join("human:alice", "human").error,
    { code: -32403, message: "An oidc_token is required to join as a human participant" });
  // The join would have made the workspace, and made nothing.
  assert.equal(c.workspaces.has("w"), false);
  assert.equal(join("human:alice", "human", { oidc_token: "alice" }).error, undefined);
  assert.equal(join("agent:bot", "agent").error, undefined);
  // A member joining again is a join like any other.
  assert.equal(join("human:alice", "human").error.code, -32403);
});

test("where a presentation verifier is configured and advertised, a person joins with a presentation", () => {
  const { join } = coordinator({ verifyVc, defaultProfiles: ["core/1.0", "review/1.0", "identity-vc/1.0"] });
  assert.deepEqual(join("human:carol", "human").error,
    { code: -32410, message: "A vc_presentation is required to join as a human participant" });
  assert.equal(join("human:carol", "human", { vc_presentation: { who: "carol" } }).error, undefined);
});

test("where both apply, either credential admits a person", () => {
  const { join } = coordinator({ verifyOidcToken, verifyVc,
                                  defaultProfiles: ["core/1.0", "review/1.0", "identity-vc/1.0"] });
  assert.deepEqual(join("human:dan", "human").error,
    { code: -32403, message: "An oidc_token or a vc_presentation is required to join as a human participant" });
  assert.equal(join("human:carol", "human", { vc_presentation: { who: "carol" } }).error, undefined);
  assert.equal(join("human:bob", "human", { oidc_token: "bob" }).error, undefined);
});

test("a workspace that does not advertise the profile takes a person without one", () => {
  const { join } = coordinator({ verifyVc });
  assert.equal(join("human:carol", "human").error, undefined);
});

// ----------------------------------------------------------- credentials off the chain

test("a join's token and presentation are recorded as their digests", () => {
  const { c, join } = coordinator({ verifyOidcToken, verifyVc, enableChain: true,
                                     defaultProfiles: ["core/1.0", "review/1.0", "identity-vc/1.0"] });
  const vp = { who: "carol", proof: { value: "secret" } };
  join("human:bob", "human", { oidc_token: "bob" });
  join("human:carol", "human", { vc_presentation: vp });
  const [bob, carol] = c.workspaces.get("w")!.audit.map((e) => e.envelope!.params as Record<string, unknown>);
  assert.equal(bob.oidc_token, "sha256:" + createHash("sha256").update("bob", "utf-8").digest("hex"));
  assert.equal(carol.vc_presentation, "sha256:" + createHash("sha256").update(canonicalize(vp)).digest("hex"));
  assert.ok(!JSON.stringify(c.workspaces.get("w")!.audit).includes("secret"));
  const verified = c.dispatch({ jsonrpc: "2.0", id: "v", method: "audit.verify_chain",
                                params: { workspace: "w" } }) as any;
  assert.equal(verified.result.status, "verified");
});

test("a signed join with a token takes effect once", () => {
  const { c } = coordinator({ verifyOidcToken });
  const envelope = { jsonrpc: "2.0" as const, id: "s", method: "participant.join",
                     sig: "ed25519:k:" + "A".repeat(86) + "==",
                     params: { workspace: "w", from: "human:bob", type: "human", oidc_token: "bob" } };
  assert.equal((c.dispatch(envelope) as any).error, undefined);
  const again = c.dispatch(envelope) as any;
  assert.equal(again.error.code, -32075);
  assert.deepEqual(again.error.data, { accepted_at_seq: 0 });
});

// --------------------------------------------------------------- the nonce binding

test("the key whose thumbprint is the token's nonce is bound, and the join's other keys are not", () => {
  assert.equal(jwkThumbprint(RFC8037_KEY), "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k");
  const tokens: Record<string, Record<string, unknown>> = {
    okp: { sub: "erin", nonce: jwkThumbprint(key("erin-1")) },
    ec: { sub: "fay", nonce: jwkThumbprint(ecKey("fay-1")) },
  };
  const { c, join } = coordinator({ verifyOidcToken: (t: string) => (tokens[t] as never) ?? null });
  join("human:erin", "human", { oidc_token: "okp", jwks: { keys: [key("erin-2"), key("erin-1")] } });
  assert.deepEqual(keysOf(c, "human:erin"), ["erin-1"]);
  join("human:fay", "human", { oidc_token: "ec", jwks: { keys: [key("fay-2"), ecKey("fay-1")] } });
  assert.deepEqual(keysOf(c, "human:fay"), ["fay-1"]);
});

test("a nonce that names no key, or two, binds none, and a cnf.jwk comes first", () => {
  const twice = jwkThumbprint(key("g-1"));
  const tokens: Record<string, Record<string, unknown>> = {
    none: { sub: "g", nonce: "not-a-thumbprint" },
    two: { sub: "h", nonce: twice },
    cnf: { sub: "i", nonce: jwkThumbprint(key("i-1")), cnf: { jwk: key("i-cnf") } },
  };
  const { c, join } = coordinator({ verifyOidcToken: (t: string) => (tokens[t] as never) ?? null });
  join("human:g", "human", { oidc_token: "none", jwks: { keys: [key("g-1"), key("g-2")] } });
  assert.deepEqual(keysOf(c, "human:g"), ["g-1", "g-2"]);
  join("human:h", "human", { oidc_token: "two", jwks: { keys: [key("h-1", "G-1"), key("h-2", "G-1")] } });
  assert.deepEqual(keysOf(c, "human:h"), ["h-1", "h-2"]);
  join("human:i", "human", { oidc_token: "cnf", jwks: { keys: [key("i-1")] } });
  assert.deepEqual(keysOf(c, "human:i"), ["i-cnf"]);
});

test("a member joining again with a key its token binds adds it", () => {
  const tokens: Record<string, Record<string, unknown>> = {
    first: { sub: "jo", cnf: { jwk: key("jo-1") } },
    second: { sub: "jo", nonce: jwkThumbprint(key("jo-2")) },
  };
  const { c, join } = coordinator({ verifyOidcToken: (t: string) => (tokens[t] as never) ?? null });
  join("human:jo", "human", { oidc_token: "first" });
  const r = join("human:jo", "human", { oidc_token: "second", jwks: { keys: [key("jo-2"), key("jo-3")] } });
  assert.equal(r.error, undefined);
  assert.deepEqual(keysOf(c, "human:jo"), ["jo-1", "jo-2"]);
});
