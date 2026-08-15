/**
 * Regression: participant.join must not replace an existing member. A re-join
 * keeps the member's role, scopes and keys, refreshes only the verified identity
 * binding, and never accepts self-asserted jwks for an already-admitted URI.
 * Guards the 0.2.9 fix.
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
  const claims: Record<string, unknown> = { good: { sub: "alice", auth_time: 111 } };
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
