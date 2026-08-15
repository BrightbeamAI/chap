/**
 * Regression: audit.verify_chain must not report a clean log as tampered when
 * the workspace has no chain. An unchained workspace is refused, and a workspace
 * whose chain was enabled mid-life replays only from its first chained entry.
 * Guards the 0.2.9 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { E } from "../src/jsonrpc.ts";

function send(c: Coordinator) {
  return (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
}

test("an unchained workspace is not reported tampered", () => {
  const c = new Coordinator({ deterministicIds: true });
  const s = send(c);
  s("workspace.create", { workspace: "w", profiles: ["core/1.0"] });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  const r = s("audit.verify_chain", { workspace: "w" });
  assert.equal(r.error.code, E.PARAMS);
  assert.ok(r.error.message.includes("Chain not enabled"));
});

test("a chain enabled mid-life verifies from the first chained entry", () => {
  const c = new Coordinator({ deterministicIds: true });
  const s = send(c);
  s("workspace.create", { workspace: "w", profiles: ["core/1.0"] });
  s("participant.join", { workspace: "w", from: "human:a", type: "human", role: "admin" });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  s("task.create", { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "agent:b" });
  s("workspace.set_profiles", { workspace: "w", from: "human:a", profiles: ["core/1.0", "audit-scitt/1.0"] });
  s("task.create", { workspace: "w", from: "human:a", kind: "k2", input: {}, assignee: "agent:b" });
  assert.equal(s("audit.verify_chain", { workspace: "w" }).result.ok, true);
});

test("a chained workspace still detects tampering", () => {
  const c = new Coordinator({ deterministicIds: true, enableChain: true });
  const s = send(c);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  s("task.create", { workspace: "w", from: "agent:b", task: "t1" });
  assert.equal(s("audit.verify_chain", { workspace: "w" }).result.ok, true);
  const ws = c.workspaces.get("w") as any;
  ws.audit[ws.audit.length - 1].envelope.params.task = "TAMPERED";
  assert.ok("error" in s("audit.verify_chain", { workspace: "w" }));
});
