import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Coordinator } from "../src/index.js";
import { contentHash } from "../src/canonical.js";
import { MemoryStore } from "../src/storage/store.js";
import type { SnapshotArtefact, TaskState } from "../src/types.js";

const fixtures = JSON.parse(readFileSync(
  new URL("../../../conformance/control-snapshot-vectors.json", import.meta.url), "utf8",
));
const schema = JSON.parse(readFileSync(
  new URL("../../../schemas/core/chap-task.schema.json", import.meta.url), "utf8",
)).$defs.Artefact;

for (const vector of fixtures.vectors) {
  test(`canonical snapshot fixture: ${vector.name}`, () => {
    const c = new Coordinator({ deterministicIds: true, deterministicClock: true,
      enableChain: true, defaultProfiles: vector.profiles });
    const responses = vector.envelopes.map((env: any) => c.dispatch(env));
    const response = responses.at(-1);
    assert.deepEqual(response, vector.expected);
    const artefact = response.result.artefact as SnapshotArtefact;
    assert.deepEqual(Object.keys(artefact).sort(), [...schema.required, "content"].sort());
    for (const field of ["id", "produced_by", "content_hash"] as const) {
      assert.match(artefact[field], new RegExp(schema.properties[field].pattern));
    }
    assert.ok(Number.isFinite(Date.parse(artefact.produced_at)));
    assert.equal(artefact.content_hash, contentHash(artefact.content));
    const ws = c.workspaces.get(vector.workspace)!;
    const stored = ws.snapshots.get(artefact.id)!;
    assert.deepEqual(stored, artefact);
    assert.notStrictEqual(stored.content, artefact.content);
    assert.equal(ws.chain_head, vector.expected_chain_head);
  });
}

function ready(store = new MemoryStore()) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true, store });
  const send = (method: string, params: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
      params: { workspace: "w", from: "human:a", ...params } });
  send("workspace.create", { profiles: ["core/1.0", "review/1.0", "control/1.0"] });
  send("participant.join", { type: "human", scopes: ["review"] });
  return { c, send, store };
}

for (const terminal of ["completed", "declined", "cancelled", "superseded"] as TaskState[]) {
  test(`open_tasks excludes ${terminal} and captures only task summary fields`, () => {
    const { c, send } = ready();
    const create = () => send("task.create", {
      kind: "draft", input: { nested: ["not captured"] }, assignee: "human:a",
    }).result.task_id;
    const openId = create();
    const settledId = create();
    c.workspaces.get("w")!.tasks.get(settledId)!.state = terminal;
    const { artefact } = send("control.snapshot", { include: ["open_tasks"] }).result;
    assert.deepEqual(artefact.content.state.open_tasks,
      [{ id: openId, kind: "draft", state: "created", assignee: "human:a" }]);
  });
}

test("snapshot content/hash stay stable across caller mutation, rollback and store restart", () => {
  const { c, send, store } = ready();
  const include = ["members", "mode_ceiling"];
  const { artefact } = send("control.snapshot", { include }).result;
  const expected = structuredClone(artefact) as SnapshotArtefact;
  const saved = c.workspaces.get("w")!.snapshots.get(artefact.id)!;
  include.push("audit");
  artefact.content.include.push("policy");
  artefact.content.state.members[0].scopes.push("caller");
  assert.deepEqual(saved, expected);
  c.workspaces.get("w")!.members.get("human:a")!.scopes!.push("live");
  const rollback = send("control.rollback", { to_snapshot_artefact_id: saved.id });
  assert.deepEqual(rollback.result.restored, ["mode_ceiling", "members"]);
  c.workspaces.get("w")!.members.get("human:a")!.scopes!.push("after-rollback");
  assert.deepEqual(saved, expected);
  assert.equal(saved.content_hash, contentHash(saved.content));
  send("control.set_mode_ceiling", { new_ceiling: "shadow" });
  const restarted = new Coordinator({ store });
  const loaded = restarted.workspaces.get("w")!.snapshots.get(saved.id)!;
  assert.deepEqual(loaded, expected);
  const reply: any = restarted.dispatch({ jsonrpc: "2.0", id: "rollback", method: "control.rollback",
    params: { workspace: "w", from: "human:a", to_snapshot_artefact_id: saved.id } });
  assert.deepEqual(reply.result.restored, ["mode_ceiling", "members"]);
  assert.equal(restarted.workspaces.get("w")!.mode_ceiling, "production");
  assert.deepEqual(restarted.workspaces.get("w")!.members.get("human:a")!.scopes, ["review"]);
  assert.deepEqual(loaded, expected);
});

test("restore normalizes a legacy flat snapshot without retaining a second representation", () => {
  const { c, send } = ready();
  const { artefact } = send("control.snapshot", { include: ["mode_ceiling"] }).result;
  const records = structuredClone(c.snapshot()) as any[];
  records[0].snapshots[0] = {
    id: artefact.id, kind: "snapshot", ts: artefact.produced_at, by: artefact.produced_by,
    ...artefact.content,
  };
  const restarted = new Coordinator();
  restarted.restore(records);
  assert.deepEqual(restarted.workspaces.get("w")!.snapshots.get(artefact.id), artefact);
});


test("legacy restoration omits optional undefined values and full task bodies before hashing", () => {
  const { c, send } = ready();
  send("participant.join", { from: "agent:b", type: "agent" });
  send("task.create", { kind: "draft", input: {}, assignee: "agent:b" });
  const { artefact } = send("control.snapshot").result;
  const records = structuredClone(c.snapshot()) as any[];
  const content = structuredClone(artefact.content);
  content.state.members[1].scopes = undefined;
  content.state.members[1].capabilities = { excluded: true };
  content.state.open_tasks[0].input = { excludedDecimal: 0.5 };
  records[0].snapshots[0] = {
    id: artefact.id, kind: "snapshot", ts: artefact.produced_at, by: artefact.produced_by,
    ...content,
  };
  const restarted = new Coordinator();
  restarted.restore(records);
  assert.deepEqual(restarted.workspaces.get("w")!.snapshots.get(artefact.id), artefact);
});


for (const reason of [undefined, "", "restore checkpoint"]) {
  test(`rollback response omits absent or empty reason: ${JSON.stringify(reason)}`, () => {
    const { send } = ready();
    const snapshot = send("control.snapshot", { include: ["mode_ceiling"] }).result;
    const reply = send("control.rollback", {
      to_snapshot_artefact_id: snapshot.snapshot_artefact_id,
      ...(reason === undefined ? {} : { reason }),
    });
    assert.deepEqual(reply.result, {
      rolled_back_to: snapshot.snapshot_artefact_id, audit_seq: snapshot.audit_seq,
      restored: ["mode_ceiling"], ...(reason ? { reason } : {}),
    });
  });
}
