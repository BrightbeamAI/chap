/**
 * SPECIFICATION 15.4: a method whose owning profile the workspace does not
 * advertise is refused.
 *
 * Before this, removing control/1.0 from a workspace advertised nothing and
 * changed nothing: the emergency brake stayed fully live. The refusal is
 * -32601, the answer a coordinator that never implemented the method would
 * give, so a deployment cannot tell from outside which of the two it is
 * talking to.
 *
 * The mirror of this file is
 * packages/coordinator-py/tests/test_profile_gate.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { ALWAYS_AVAILABLE, OWNING_PROFILE } from "../src/catalogue.ts";

const CORE = ["core/1.0", "review/1.0"];

function ready(profiles: string[] = CORE, options: Record<string, unknown> = {}) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                              defaultProfiles: profiles, ...options } as never);
  const send = (method: string, params: Record<string, unknown> = {}, from = "human:a"): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from, ...params } } as never);
  send("workspace.create", { profiles });
  for (const [uri, type] of [["human:a", "human"], ["agent:b", "agent"]]) {
    send("participant.join", { type }, uri);
  }
  return { c, send };
}

// ------------------------------------------------------------------ the gate

for (const [method, owner] of [
  ["control.pause", "control/1.0"],
  ["whisper.ask", "whisper/1.0"],
  ["deliberate.open", "deliberation/1.0"],
  ["handoff.propose", "handoff/1.0"],
  ["task.route", "routing/1.0"],
  ["audit.submit_to_scitt", "audit-scitt/1.0"],
]) {
  test(`${method} is refused when ${owner} is not advertised`, () => {
    const { send } = ready();
    const refused = send(method, { task_id: "t" });

    assert.equal(refused.error.code, -32601);
    assert.equal(refused.error.message, `Unknown method: ${method}`);
    assert.deepEqual(refused.error.data, { profile: owner, advertised: CORE });
  });
}

test("the same method works once the profile is advertised", () => {
  const { c, send } = ready([...CORE, "control/1.0"]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  assert.equal(send("control.pause", { task_id: id, reason: "hold" }).error, undefined);
  assert.equal((c.workspaces.get("w") as any).tasks.get(id).state, "paused");
});

test("a refusal writes nothing to the chain", () => {
  const { c, send } = ready();
  const ws = c.workspaces.get("w") as any;
  const before = ws.audit.length;
  send("control.pause", { task_id: "t", reason: "hold" });
  assert.equal(ws.audit.length, before);
});

test("a later minor version of the owning profile still carries its methods", () => {
  // A workspace on control/1.1 has the control methods. Gating on the exact
  // version string would refuse them and make every minor release a break.
  const { send } = ready([...CORE, "control/1.1"]);
  const id = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  assert.equal(send("control.pause", { task_id: id, reason: "hold" }).error, undefined);
});

// -------------------------------------------------------------- the carve-outs

for (const method of [...ALWAYS_AVAILABLE].sort()) {
  test(`${method} is never gated`, () => {
    // A workspace must be able to ask what it is and to check its own chain.
    // audit.verify_chain belongs to audit-scitt/1.0 while chaining also turns
    // on through an option, so gating it would let a workspace write a chain
    // it is refused permission to verify.
    const { send } = ready(CORE, { enableChain: true });
    const r = send(method, method === "audit.verify_receipt" ? { receipt: {} } : {});
    assert.notEqual(r.error?.code, -32601, `${method} was gated`);
  });
}

for (const method of ["participant.rotate_key", "participant.revoke_key"]) {
  test(`${method} is never gated`, () => {
    // The shipped MCP server advertises nine profiles and security-signed/1.0
    // is not among them. Gating these would remove an operator's response to a
    // compromised key from every default deployment, so the catalogue
    // attributes them to core/1.0.
    assert.equal(OWNING_PROFILE[method], "core/1.0");
    const { send } = ready();
    const r = send(method, { target_uri: "human:a", kid: "k-1" });
    assert.notEqual(r.error?.code, -32601);
  });
}

test("every implemented method has an owner", () => {
  // A method the catalogue does not name would pass the gate unexamined.
  const { c } = ready();
  for (const method of (c as any).handlers.keys()) {
    assert.ok(OWNING_PROFILE[method], `${method} is dispatchable and not in the catalogue`);
  }
});

// ------------------------------------- advertised against enforced, at create

test("signing on adds the profile it enforces", () => {
  const c = new Coordinator({ requireSignatures: true } as never);
  c.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create",
               params: { workspace: "w", profiles: ["core/1.0"] } } as never);
  assert.ok((c.workspaces.get("w") as any).profiles.includes("security-signed/1.0"));
});

test("advertising a security profile without enforcing it is refused", () => {
  const c = new Coordinator({} as never);
  const refused = c.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create",
    params: { workspace: "w", profiles: ["core/1.0", "security-signed/1.0"] } } as never) as any;
  assert.equal(refused.error.code, -32602);
  assert.match(refused.error.message, /requireSignatures/);
  assert.equal(c.workspaces.has("w"), false);
});

test("the same rule holds for identity-oidc", () => {
  const enforcing = new Coordinator({ verifyOidcToken: () => ({}) } as never);
  enforcing.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create",
                       params: { workspace: "w", profiles: ["core/1.0"] } } as never);
  assert.ok((enforcing.workspaces.get("w") as any).profiles.includes("identity-oidc/1.0"));

  const bare = new Coordinator({} as never);
  const refused = bare.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create",
    params: { workspace: "w", profiles: ["core/1.0", "identity-oidc/1.0"] } } as never) as any;
  assert.equal(refused.error.code, -32602);
});
