/**
 * Signature checks follow the workspace, and the "humans" policy.
 *
 * Signatures were checked only where the coordinator option was on. An admin's
 * workspace.set_profiles could add security-signed/1.0 and unsigned calls were
 * still accepted, and a store written under the option and reopened without it
 * advertised the profile and accepted unsigned calls (29 October and 6 November
 * reviews). A workspace now requires signatures when it advertises the profile
 * or the option is on, cannot stop advertising it, and under the "humans"
 * policy takes unsigned calls from its agents.
 *
 * Mirrors packages/coordinator-py/tests/test_signature_policy.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator, type CoordinatorOptions } from "../src/coordinator.js";
import { canonicalize } from "../src/canonical.js";
import { deriveKeypair, signEnvelope } from "../src/crypto.js";
import { MemoryStore } from "../src/storage/store.js";
import type { Envelope } from "../src/types.js";

const PROFILES = ["core/1.0", "review/1.0"];
const SIGNED = [...PROFILES, "security-signed/1.0"];

function ready(options: CoordinatorOptions = {}, create: Record<string, unknown> = { profiles: PROFILES }) {
  const c = new Coordinator({ deterministicIds: true, ...options });
  let n = 0;
  const call = (method: string, from: string, params: Record<string, unknown> = {}): Envelope =>
    ({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", from, ...params } });
  const unsigned = (method: string, from: string, params: Record<string, unknown> = {}): any =>
    c.dispatch(call(method, from, params));
  const signed = (method: string, from: string, params: Record<string, unknown> = {}): any => {
    const env = call(method, from, params);
    const { privateKey, jwk } = deriveKeypair(from);
    return c.dispatch({ ...env, sig: signEnvelope(canonicalize(env as never), privateKey, jwk.kid) });
  };
  unsigned("workspace.create", "human:a", create);
  for (const [uri, type, role] of [["human:a", "human", "admin"], ["human:c", "human", "reviewer"],
                                   ["agent:b", "agent", "drafter"]]) {
    unsigned("participant.join", uri, { type, role, jwks: { keys: [deriveKeypair(uri).jwk] } });
  }
  return { c, unsigned, signed, ws: () => c.workspaces.get("w")! };
}

const task = { kind: "k", input: {}, assignee: "agent:b" };

test("adding security-signed/1.0 with set_profiles turns the checks on", () => {
  const { unsigned } = ready();
  assert.ok(unsigned("workspace.set_profiles", "human:a", { profiles: SIGNED }).result);
  assert.equal(unsigned("task.create", "agent:b", task).error?.code, -32070);
});

test("a store reopened without the option still requires signatures", () => {
  const store = new MemoryStore();
  ready({ store, requireSignatures: true });
  const again = new Coordinator({ store });
  const r = again.dispatch({ jsonrpc: "2.0", id: "x", method: "task.create",
                             params: { workspace: "w", from: "agent:b", ...task } }) as any;
  assert.equal(r.error?.code, -32070);
});

test("a workspace that advertises security-signed/1.0 cannot stop advertising it", () => {
  const { signed, ws } = ready({}, { profiles: SIGNED });
  const r = signed("workspace.set_profiles", "human:a", { profiles: PROFILES });
  assert.deepEqual(r.error, { code: -32011, message: "security-signed/1.0 cannot be removed once advertised" });
  assert.deepEqual(ws().profiles, SIGNED);
  // A later version of the profile keeps it advertised.
  assert.ok(signed("workspace.set_profiles", "human:a", { profiles: [...PROFILES, "security-signed/1.1"] }).result);
});

test("under the humans policy an agent's unsigned call is taken and a person's is not", () => {
  const { unsigned, signed, c } = ready({}, { profiles: SIGNED, signature_policy: "humans" });
  const created = unsigned("task.create", "agent:b", { ...task, review_required: true });
  assert.equal(created.error, undefined);
  const id = created.result.task_id;
  assert.equal(unsigned("task.complete", "agent:b", { task_id: id, output: { v: 1 } }).result.state,
               "review_requested");
  assert.equal(unsigned("decide.approve", "human:a", { task_id: id }).error?.code, -32070);
  // A call that is signed is checked, from an agent as from anyone.
  const forged = c.dispatch({ jsonrpc: "2.0", id: "f", method: "task.update", sig: "ed25519:x:AAAA",
    params: { workspace: "w", from: "agent:b", task_id: id, state: "in_progress" } } as Envelope) as any;
  assert.equal(forged.error?.code, -32071);
  // A sender who is not a member is not taken unsigned.
  assert.equal(unsigned("task.create", "agent:ghost", task).error?.code, -32070);
  assert.equal(signed("decide.approve", "human:a", { task_id: id }).result.state, "completed");
});

test("the policy comes from workspace.create, else the option, and describe returns it", () => {
  const named = ready({}, { profiles: SIGNED, signature_policy: "humans" });
  assert.equal(named.signed("workspace.describe", "human:a").result.signature_policy, "humans");
  const fallback = ready({ signaturePolicy: "humans" }, { profiles: SIGNED });
  assert.equal(fallback.ws().signature_policy, "humans");
  const plain = ready();
  assert.equal(plain.unsigned("workspace.describe", "human:a").result.signature_policy, "all");
  const c = new Coordinator();
  const r = c.dispatch({ jsonrpc: "2.0", id: "x", method: "workspace.create",
                         params: { workspace: "w", signature_policy: "agents" } }) as any;
  assert.deepEqual(r.error, { code: -32602, message: "signature_policy must be all or humans" });
  // The params are checked before the name is, as they are in Python.
  const taken = plain.c.dispatch({ jsonrpc: "2.0", id: "y", method: "workspace.create",
                                   params: { workspace: "w", signature_policy: 5 } }) as any;
  assert.deepEqual(taken.error, { code: -32602, message: "signature_policy must be all or humans" });
});

test("the policy is kept by the store, and a workspace stored without one verifies every call", async () => {
  const store = new MemoryStore();
  ready({ store }, { profiles: SIGNED, signature_policy: "humans" });
  assert.equal(new Coordinator({ store }).workspaces.get("w")!.signature_policy, "humans");
  const { c } = ready({}, { profiles: SIGNED });
  const snapshot = JSON.parse(JSON.stringify(c.snapshot()));
  delete snapshot[0].signature_policy;
  const again = new Coordinator();
  again.restore(snapshot);
  assert.equal(again.workspaces.get("w")!.signature_policy, "all");
});
