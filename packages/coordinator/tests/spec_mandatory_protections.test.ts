/**
 * SPECIFICATION 15.1 is checked against the coordinator, the way 8.1 is.
 *
 * The section listed eight flat obligations on the Coordinator, and three of
 * them were not that: signature verification and step-up are turned on by a
 * profile, TLS and delivery filtering belong to the deployment, and a scope
 * check is declared per method and enforced by neither reference. A
 * requirement the references do not meet is worse than none, because the
 * references are what conformance is measured against.
 *
 * This holds each remaining unconditional claim to the code, and holds the
 * descriptor schema to what the coordinator sends. The mirror is
 * packages/coordinator-py/tests/test_spec_mandatory_protections.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Coordinator } from "../src/coordinator.ts";

const SPEC = readFileSync(new URL("../../../SPECIFICATION.md", import.meta.url), "utf8");
const WORKSPACE_SCHEMA = JSON.parse(readFileSync(
  new URL("../../../schemas/core/chap-workspace.schema.json", import.meta.url), "utf8"));

const PROFILES = ["core/1.0", "review/1.0", "modes/1.0", "control/1.0"];

function ready(options: Record<string, unknown> = {}) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
                              defaultProfiles: PROFILES, ...options } as never);
  const send = (method: string, params: Record<string, unknown> = {}, from = "human:a"): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from, ...params } } as never);
  send("workspace.create", { profiles: PROFILES });
  send("participant.join", { type: "human", role: "admin" }, "human:a");
  send("participant.join", { type: "agent", role: "drafter" }, "agent:b");
  return { c, send };
}

/** The section with its line wrapping collapsed, so a phrase can be sought. */
function section(heading: string, until: string): string {
  return SPEC.slice(SPEC.indexOf(heading), SPEC.indexOf(until)).split(/\s+/).join(" ");
}

// ------------------------------------------------ the section says what it says

test("the section separates who has to do the work", () => {
  // A flat list of MUSTs on the Coordinator is what let three requirements sit
  // there unmet. The grouping is the fix, so it is held in place.
  const body = section("### 15.1 Mandatory protections", "### 15.2");
  assert.ok(body.includes("A conformant Coordinator MUST"));
  assert.ok(body.includes("A profile turns these on"));
  assert.ok(body.includes("The deployment MUST"));
});

test("the section claims no scope enforcement", () => {
  // Declared per method in the catalogue, enforced by neither reference.
  const body = section("### 15.1 Mandatory protections", "### 15.2");
  assert.ok(body.includes("not yet enforced by either reference"));
});

// --------------------------------------------- the unconditional ones are true

test("the chain is ordered by acceptance, not by the sender's clock", () => {
  const { c, send } = ready({ enableChain: true });
  for (const ts of ["2030-01-01T00:00:00.000Z", "2020-01-01T00:00:00.000Z"]) {
    send("task.create", { kind: "k", input: {}, assignee: "agent:b", ts });
  }
  const audit = (c.workspaces.get("w") as any).audit as Array<{ seq: number; arrived: string }>;
  assert.deepEqual(audit.map(e => e.seq), [...audit.map(e => e.seq)].sort((a, b) => a - b));
  for (let i = 1; i < audit.length; i++) assert.ok(audit[i - 1].arrived <= audit[i].arrived);
});

test("every accepted operation is recorded and the reads are not", () => {
  const { c, send } = ready();
  const ws = c.workspaces.get("w") as any;
  const before = ws.audit.length;
  send("task.create", { kind: "k", input: {}, assignee: "agent:b" });
  assert.equal(ws.audit.length, before + 1);
  for (const read of ["workspace.describe", "audit.read"]) send(read);
  assert.equal(ws.audit.length, before + 1);
});

test("the mode ceiling is enforced and the change is recorded", () => {
  const { c, send } = ready();
  const ws = c.workspaces.get("w") as any;
  const before = ws.audit.length;
  assert.equal(send("control.set_mode_ceiling", { new_ceiling: "trial" }).error, undefined);
  assert.equal(ws.audit.length, before + 1, "a mode change is a first-class entry");

  const refused = send("task.create", { kind: "k", input: {}, assignee: "agent:b", mode: "production" });
  assert.equal(refused.error.code, -32040);
});

test("a role check the method defines is enforced", () => {
  const { send } = ready();
  assert.equal(send("workspace.set_profiles", { profiles: PROFILES }).error, undefined);
  const refused = send("workspace.set_profiles", { profiles: PROFILES }, "agent:b");
  assert.match(refused.error.message, /admin/);
});

test("ids are random outside test mode", () => {
  const live = new Coordinator({} as never);
  const minted = new Set(Array.from({ length: 64 }, () => (live as any).ids.taskId()));
  assert.equal(minted.size, 64);
  for (const id of minted) assert.match(id as string, /^tsk_[0-9A-HJKMNP-TV-Z]{26}$/);
});

// ------------------------------------------- the conditional ones are optional

test("signature verification is what the profile turns on", () => {
  const { send } = ready();
  assert.equal(send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).error, undefined,
               "an unsigned envelope is accepted where the profile is not in force");

  const signed = new Coordinator({ requireSignatures: true } as never);
  signed.dispatch({ jsonrpc: "2.0", id: "c", method: "workspace.create",
                    params: { workspace: "w", profiles: ["core/1.0"] } } as never);
  // 6.5: enforcement on adds the profile, so the descriptor cannot understate.
  assert.ok((signed.workspaces.get("w") as any).profiles.includes("security-signed/1.0"));
});

// ------------------------------------------ the descriptor matches its schema

test("the descriptor carries what its schema requires", () => {
  for (const chained of [false, true]) {
    const { send } = ready({ enableChain: chained });
    const descriptor = send("workspace.describe").result;
    for (const field of WORKSPACE_SCHEMA.required) {
      assert.ok(field in descriptor, `${field} is required and not sent (chain=${chained})`);
    }
    for (const field of Object.keys(descriptor)) {
      assert.ok(field in WORKSPACE_SCHEMA.properties,
                `${field} is sent and not declared (chain=${chained})`);
    }
  }
  // The head exists only where there is a chain, which is why it is optional.
  assert.ok(!WORKSPACE_SCHEMA.required.includes("evidence_head"));
});
