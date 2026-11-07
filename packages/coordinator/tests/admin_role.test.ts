/**
 * The admin role is granted, never claimed (SPECIFICATION 6.3).
 *
 * Any joiner could ask for `role: "admin"` and get it, an agent included, and
 * with it drop the workspace's identity profile through
 * workspace.set_profiles or revoke a person's key through
 * participant.revoke_key. The first participant to join may take the role;
 * nobody else can ask for it, and only a person or a service holds it.
 * Mirrors test_admin_role.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";

function coordinator() {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                              defaultProfiles: ["core/1.0", "review/1.0", "control/1.0"] });
  let n = 0;
  const call = (method: string, params: Record<string, unknown>): any =>
    c.dispatch({ jsonrpc: "2.0", id: `c${++n}`, method, params: { workspace: "w", ...params } });
  const join = (from: string, type: string, extra: Record<string, unknown> = {}) =>
    call("participant.join", { from, type, ...extra });
  const roleOf = (uri: string) => c.workspaces.get("w")!.members.get(uri)?.role;
  return { c, call, join, roleOf };
}

const GRANTED = { code: -32011, message: "The admin role is granted by an admin" };

test("the first participant to join may take the admin role, and nobody after", () => {
  const { c, call, join, roleOf } = coordinator();
  call("workspace.create", {});
  assert.deepEqual(join("human:a", "human", { role: "admin" }).result, { joined: true, as: "human:a" });
  assert.equal(roleOf("human:a"), "admin");
  assert.deepEqual(join("human:b", "human", { role: "admin" }).error, GRANTED);
  assert.equal(c.workspaces.get("w")!.members.has("human:b"), false);
  // Asking for another role is a join like any other.
  assert.equal(join("human:b", "human", { role: "reviewer" }).error, undefined);
  assert.equal(roleOf("human:b"), "reviewer");
});

test("a join that makes its workspace is the first member", () => {
  const { join, roleOf } = coordinator();
  assert.equal(join("service:operator", "service", { role: "admin" }).error, undefined);
  assert.equal(roleOf("service:operator"), "admin");
});

test("only a person or a service holds the admin role", () => {
  const { call, join } = coordinator();
  call("workspace.create", {});
  const held = { code: -32602, message: "The admin role is held by a human or a service" };
  assert.deepEqual(join("agent:bot", "agent", { role: "admin" }).error, held);
  assert.deepEqual(join("group:ops", "group", { role: "admin" }).error, held);
});

test("an agent that asks for the role cannot then change the profiles or revoke a person's key", () => {
  const { call, join, roleOf } = coordinator();
  call("workspace.create", {});
  join("human:alice", "human", { jwks: { keys: [{ kty: "OKP", crv: "Ed25519", kid: "k-alice", x: "AAAA" }] } });
  assert.deepEqual(join("agent:bot", "agent", { role: "admin" }).error.code, -32602);
  join("agent:bot", "agent");
  assert.equal(roleOf("agent:bot"), "participant");
  assert.equal(call("workspace.set_profiles", { from: "agent:bot", profiles: ["core/1.0"] }).error.code, -32011);
  assert.equal(call("participant.revoke_key", { from: "agent:bot", target_uri: "human:alice", kid: "k-alice" })
    .error.code, -32011);
});

test("a workspace whose members have all left has had its first member", () => {
  const { call, join } = coordinator();
  call("workspace.create", {});
  join("human:a", "human", { role: "admin" });
  call("participant.leave", { from: "human:a" });
  assert.deepEqual(join("human:m", "human", { role: "admin" }).error, GRANTED);
});

test("a member joining again keeps its role, whatever role it names", () => {
  const { call, join, roleOf } = coordinator();
  call("workspace.create", {});
  join("human:a", "human", { role: "admin" });
  join("human:b", "human");
  assert.deepEqual(join("human:b", "human", { role: "admin" }).result, { joined: true, as: "human:b" });
  assert.equal(roleOf("human:b"), "participant");
});

test("a rollback neither grants the admin role nor takes it away", () => {
  const { call, join, roleOf } = coordinator();
  call("workspace.create", {});
  join("human:a", "human", { role: "admin" });
  join("human:b", "human", { role: "reviewer" });
  const snap = call("control.snapshot", { from: "human:a", include: ["members"] }).result.snapshot_artefact_id;
  // The admin leaves, and someone else takes the name.
  call("participant.leave", { from: "human:a" });
  join("human:a", "human");
  call("participant.leave", { from: "human:b" });
  join("human:b", "human", { role: "drafter" });
  const r = call("control.rollback", { from: "human:b", to_snapshot_artefact_id: snap });
  assert.deepEqual(r.result.restored, ["members"]);
  assert.equal(roleOf("human:a"), "participant");
  // Roles other than admin are restored as before.
  assert.equal(roleOf("human:b"), "reviewer");
  assert.equal(call("workspace.set_profiles", { from: "human:a", profiles: ["core/1.0"] }).error.code, -32011);
});
