/**
 * Who signs, who joins, and which profiles stay (SPECIFICATION 5.2, 5.1,
 * 6.3.3 and 6.5).
 *
 * Under the "humans" policy a service called unsigned, though only an agent
 * on the server's own transport needs to. A person or service could join a
 * workspace that requires signatures with no key, and hold a name it could
 * never sign for. group: and workspace: URIs, which address everyone a review
 * or handoff covers, could join as members, and a workspace:<id> member then
 * stood in for the broadcast scope. An admin could drop identity-oidc/1.0 or
 * identity-vc/1.0 and switch off the check that people are who they say, and
 * any string passed as a profile id, security-signed/0.0-off included.
 * Mirrors test_join_rules.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator, type CoordinatorOptions } from "../src/coordinator.js";
import { canonicalize } from "../src/canonical.js";
import { deriveKeypair, signEnvelope } from "../src/crypto.js";
import type { Envelope } from "../src/types.js";

function ready(options: CoordinatorOptions = {}) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true, ...options });
  let n = 0;
  const env = (method: string, from: string, params: Record<string, unknown>): Envelope =>
    ({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from, ...params } });
  const call = (method: string, from: string, params: Record<string, unknown> = {}): any =>
    c.dispatch(env(method, from, params));
  const signed = (method: string, from: string, params: Record<string, unknown> = {}): any => {
    const e = env(method, from, params);
    const { privateKey, jwk } = deriveKeypair(from);
    return c.dispatch({ ...e, sig: signEnvelope(canonicalize(e as never), privateKey, jwk.kid) });
  };
  const keys = (uri: string) => ({ jwks: { keys: [deriveKeypair(uri).jwk] } });
  return { c, call, signed, keys };
}

const SIGNED = ["core/1.0", "review/1.0", "security-signed/1.0"];

test("under the humans policy only an agent calls unsigned; people and services sign", () => {
  const { call, signed, keys } = ready();
  call("workspace.create", "human:a", { profiles: SIGNED, signature_policy: "humans" });
  call("participant.join", "human:a", { type: "human", role: "admin", ...keys("human:a") });
  call("participant.join", "service:ops", { type: "service", ...keys("service:ops") });
  call("participant.join", "agent:b", { type: "agent" });
  assert.equal(call("workspace.describe", "agent:b").error, undefined);
  assert.equal(call("workspace.describe", "service:ops").error.code, -32070);
  assert.equal(signed("workspace.describe", "service:ops").error, undefined);
  assert.equal(call("workspace.describe", "human:a").error.code, -32070);
});

test("in a workspace that requires signatures, a participant who signs joins with a key", () => {
  const { c, call, signed, keys } = ready();
  call("workspace.create", "human:a", { profiles: SIGNED, signature_policy: "humans" });
  assert.deepEqual(call("participant.join", "human:a", { type: "human", role: "admin" }).error,
    { code: -32602, message: "human:a must sign in this workspace, and joins with no key" });
  assert.equal(call("participant.join", "human:a", { type: "human", role: "admin", ...keys("human:a") }).error,
    undefined);
  assert.equal(call("participant.join", "service:ops", { type: "service" }).error.code, -32602);
  // Under "humans" an agent calls unsigned, so it needs no key.
  assert.equal(call("participant.join", "agent:b", { type: "agent" }).error, undefined);
  // Keys an invitation pins count.
  signed("workspace.invite", "human:a", { uri: "human:c", type: "human", ...keys("human:c") });
  assert.equal(call("participant.join", "human:c", { type: "human" }).error, undefined);
  assert.deepEqual([...c.workspaces.get("w")!.members.keys()], ["human:a", "agent:b", "human:c"]);
});

test("under the all policy an agent joins with a key too", () => {
  const { call, keys } = ready({ requireSignatures: true });
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin", ...keys("human:a") });
  assert.equal(call("participant.join", "agent:b", { type: "agent" }).error.code, -32602);
  assert.equal(call("participant.join", "agent:b", { type: "agent", ...keys("agent:b") }).error, undefined);
});

test("a key a token binds is a key to join with", () => {
  const cnf = deriveKeypair("human:p").jwk;
  const { c, call } = ready({ requireSignatures: true,
    verifyOidcToken: (t: string) => (t === "p" ? { sub: "p", chap_participant_uri: "human:p", cnf: { jwk: cnf } } : null) });
  call("workspace.create", "human:p");
  assert.equal(call("participant.join", "human:p", { type: "human", oidc_token: "p" }).error, undefined);
  assert.deepEqual(c.workspaces.get("w")!.members.get("human:p")!.keys.map((k) => k.kid), [cnf.kid]);
});

test("group: and workspace: URIs name address scopes, which neither join nor are invited", () => {
  const { c, call } = ready();
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin" });
  const scopes = { code: -32602, message: "group: and workspace: URIs name address scopes, which cannot join" };
  assert.deepEqual(call("participant.join", "workspace:w", { type: "workspace" }).error, scopes);
  assert.deepEqual(call("participant.join", "group:ops", { type: "group" }).error, scopes);
  assert.deepEqual(call("workspace.invite", "human:a", { uri: "group:ops", type: "group" }).error, scopes);
  assert.deepEqual([...c.workspaces.get("w")!.members.keys()], ["human:a"]);
});

test("an identity profile, once advertised, stays", () => {
  const verifyOidcToken = (t: string) => ({ sub: t, chap_participant_uri: `human:${t}` });
  const verifyVc = (vp: Record<string, unknown>) => ({ holder: vp.who });
  const { call } = ready({ verifyOidcToken, verifyVc });
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin", oidc_token: "a" });
  const set = (profiles: string[]) => call("workspace.set_profiles", "human:a", { profiles });
  assert.deepEqual(set(["core/1.0", "review/1.0", "identity-vc/1.0"]).error,
    { code: -32011, message: "identity-oidc/1.0 cannot be removed once advertised" });
  assert.deepEqual(set(["core/1.0", "review/1.0", "identity-oidc/1.0"]).error,
    { code: -32011, message: "identity-vc/1.0 cannot be removed once advertised" });
  assert.equal(set(["core/1.0", "review/1.0", "identity-oidc/1.0", "identity-vc/1.1"]).error, undefined);
});

test("a profile an admin adds names its verifier", () => {
  const { call } = ready();
  call("workspace.create", "human:a");
  call("participant.join", "human:a", { type: "human", role: "admin" });
  assert.equal(call("workspace.set_profiles", "human:a",
    { profiles: ["core/1.0", "identity-oidc/1.0"] }).error.code, -32602);
  assert.equal(call("workspace.set_profiles", "human:a",
    { profiles: ["core/1.0", "identity-vc/1.0"] }).error.code, -32602);
});

test("a profile id names a catalogued profile at its major version", () => {
  const { c, call } = ready();
  call("workspace.create", "human:a", { profiles: ["core/1.0", "review/1.0"] });
  call("participant.join", "human:a", { type: "human", role: "admin" });
  const unknown = (id: string) => ({ code: -32602,
    message: `Unknown profile ${JSON.stringify(id)}: a profile id is <name>/<major>.<minor> for a profile the catalogue lists` });
  for (const id of ["security-signed/0.0-off", "security-signed/2.0", "security-signed", "x-acme/1.0",
                    "review/١.0", "review/01.0", "Review/1.0"]) {
    assert.deepEqual(call("workspace.set_profiles", "human:a", { profiles: ["core/1.0", id] }).error, unknown(id), id);
    assert.deepEqual(new Coordinator({}).dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create",
      params: { workspace: "v", profiles: ["core/1.0", id] } }).error, unknown(id), id);
  }
  assert.deepEqual(c.workspaces.get("w")!.profiles, ["core/1.0", "review/1.0"]);
  // A later minor version is the same profile (SPECIFICATION 15.4).
  assert.equal(call("workspace.set_profiles", "human:a", { profiles: ["core/1.0", "review/1.1"] }).error, undefined);
  assert.equal(new Coordinator({}).dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create",
    params: { workspace: "v", profiles: ["core/1.0", "control/1.1"] } }).error, undefined);
});
