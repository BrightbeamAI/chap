import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

const PROFILES = ["core/1.0", "review/1.0", "control/1.0"];

function ready() {
  const c = new Coordinator({ defaultProfiles: PROFILES, deterministicIds: true, enableChain: true });
  const send = (method: string, params: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
      params: { workspace: "w", from: "human:a", ...params } });
  send("workspace.create", { profiles: PROFILES });
  send("participant.join", { type: "human", scopes: ["review"] });
  return { c, send };
}

test("snapshot empty include is refused without state or audit mutation", () => {
  const { c, send } = ready();
  const before = structuredClone(c.snapshot());
  const reply = send("control.snapshot", { include: [] });
  assert.equal(reply.error.code, -32602);
  assert.equal("result" in reply, false);
  assert.deepEqual(c.snapshot(), before);
  // Failed capture must not allocate an artefact id either.
  const retry = send("control.snapshot", { include: ["mode_ceiling"] });
  const fresh = ready();
  const expected = fresh.send("control.snapshot", { include: ["mode_ceiling"] });
  assert.equal(retry.result.snapshot_artefact_id, expected.result.snapshot_artefact_id);
});

test("snapshot omitted include uses the defaults", () => {
  const { c, send } = ready();
  const { artefact } = send("control.snapshot").result;
  // Envelope conformance is covered separately; selection is unchanged by #148.
  const content = artefact.content ?? artefact;
  assert.deepEqual([...content.include].sort(),
    ["members", "mode_ceiling", "open_tasks"]);
});

test("snapshot nonempty include captures only the selected slice", () => {
  const { c, send } = ready();
  const { artefact } = send("control.snapshot", { include: ["mode_ceiling"] }).result;
  const content = artefact.content ?? artefact;
  assert.deepEqual(content.include, ["mode_ceiling"]);
  assert.deepEqual(content.state, { mode_ceiling: c.workspaces.get("w")!.mode_ceiling });
});

test("rollback empty selection is refused without state or audit mutation", () => {
  const { c, send } = ready();
  const sid = send("control.snapshot", { include: ["members", "mode_ceiling"] }).result.snapshot_artefact_id;
  const ws = c.workspaces.get("w")!;
  ws.mode_ceiling = "shadow";
  ws.members.get("human:a")!.scopes = ["changed"];
  const before = structuredClone(c.snapshot());
  const reply = send("control.rollback", { to_snapshot_artefact_id: sid, what_to_restore: [] });
  assert.equal(reply.error.code, -32602);
  assert.equal("result" in reply, false);
  assert.deepEqual(c.snapshot(), before);
});

for (const partial of [false, true]) {
  test(partial ? "nonempty rollback restores only the subset" : "omitted rollback restores captured slices", () => {
    const { c, send } = ready();
    const ws = c.workspaces.get("w")!;
    const originalCeiling = ws.mode_ceiling;
    const sid = send("control.snapshot", { include: ["members", "mode_ceiling"] }).result.snapshot_artefact_id;
    ws.mode_ceiling = "shadow";
    ws.members.get("human:a")!.scopes = ["changed"];
    const reply = send("control.rollback", {
      to_snapshot_artefact_id: sid,
      ...(partial ? { what_to_restore: ["mode_ceiling"] } : {}),
    });
    assert.deepEqual([...reply.result.restored].sort(), partial ? ["mode_ceiling"] : ["members", "mode_ceiling"]);
    assert.equal(ws.mode_ceiling, originalCeiling);
    assert.deepEqual(ws.members.get("human:a")!.scopes, partial ? ["changed"] : ["review"]);
  });
}
