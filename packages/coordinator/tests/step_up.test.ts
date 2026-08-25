/**
 * Regression: step-up fails closed for OIDC actors (humans / members with an OIDC
 * binding) and enforces min_acr; agents and services without an OIDC binding are
 * exempt. Mirrors packages/coordinator-py/tests/test_step_up.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

function privileged(c: Coordinator, from: string) {
  return c.dispatch({ jsonrpc: "2.0", id: "p", method: "workspace.set_profiles",
    params: { workspace: "w", from, profiles: ["core/1.0", "review/1.0"] }});
}

test("step-up denies an OIDC human without fresh auth", () => {
  const c = new Coordinator({ enforceStepUp: true });
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create", params: { workspace: "w" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human" }});
  assert.equal(privileged(c, "human:alice").error?.code, -32402);
});

test("step-up exempts a non-OIDC agent", () => {
  const c = new Coordinator({ enforceStepUp: true });
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create", params: { workspace: "w" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "agent:bot", type: "agent" }});
  assert.notEqual(privileged(c, "agent:bot").error?.code, -32402);
});

test("step-up enforces min_acr", () => {
  const fresh = Math.floor(Date.now() / 1000);
  const c = new Coordinator({ enforceStepUp: true,
    verifyOidcToken: (t: string) => ({ sub: "u", auth_time: fresh, acr: t }) });
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "w", min_acr: "mfa" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human", oidc_token: "pwd" }});
  c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.join",
    params: { workspace: "w", from: "human:bob", type: "human", oidc_token: "mfa" }});
  assert.equal(privileged(c, "human:alice").error?.code, -32402);
  assert.notEqual(privileged(c, "human:bob").error?.code, -32402);
});

test("min_acr is not bypassed by a downgraded re-join", () => {
  const fresh = Math.floor(Date.now() / 1000);
  const c = new Coordinator({ enforceStepUp: true,
    verifyOidcToken: (t: string) => ({ sub: "u", auth_time: fresh, acr: t === "strong" ? "mfa" : "pwd" }) });
  c.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace: "w", min_acr: "mfa" }});
  c.dispatch({ jsonrpc: "2.0", id: "2", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human", role: "admin", oidc_token: "strong" }});
  assert.notEqual(privileged(c, "human:alice").error?.code, -32402);

  // Re-join with a downgraded token: fresh auth_time but a weaker acr.
  c.dispatch({ jsonrpc: "2.0", id: "3", method: "participant.join",
    params: { workspace: "w", from: "human:alice", type: "human", oidc_token: "weak" }});
  assert.equal(privileged(c, "human:alice").error?.code, -32402);
});
