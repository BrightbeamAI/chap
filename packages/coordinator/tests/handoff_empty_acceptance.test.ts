import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/index.js";

function ready() {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true, enableChain: true });
  const send = (method: string, params: Record<string, unknown> = {}): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
      params: { workspace: "w", from: "human:alice", ...params } });
  send("workspace.create", { profiles: ["core/1.0", "review/1.0", "handoff/1.0"] });
  send("participant.join", { type: "human" });
  send("participant.join", { from: "human:bob", type: "human" });
  const taskIds: string[] = Array.from({ length: 2 }, () => send("task.create", {
    kind: "handoff", input: {}, assignee: "human:alice",
  }).result.task_id);
  const handoffId = send("handoff.propose", {
    to: "human:bob", tasks: taskIds.map(task_id => ({ task_id })),
  }).result.handoff_id;
  return { c, send, taskIds, handoffId };
}

test("empty acceptance is refused without state or audit mutation", () => {
  const { c, send, taskIds, handoffId } = ready();
  const before = structuredClone(c.snapshot());
  const reply = send("handoff.accept", {
    from: "human:bob", handoff_id: handoffId, accepted_task_ids: [],
  });
  assert.equal(reply.error.code, -32602);
  assert.equal("result" in reply, false);
  // Includes the handoff state, task ownership/history, audit and chain head.
  assert.deepEqual(c.snapshot(), before);
  // Refusal leaves the proposal available for a later genuine acceptance.
  const retry = send("handoff.accept", { from: "human:bob", handoff_id: handoffId });
  assert.deepEqual(retry.result.task_ids, taskIds);
});

for (const partial of [false, true]) {
  test(partial ? "nonempty acceptance transfers only the subset" : "omitted acceptance transfers all", () => {
    const { c, send, taskIds, handoffId } = ready();
    const selected = partial ? taskIds.slice(0, 1) : taskIds;
    const reply = send("handoff.accept", {
      from: "human:bob", handoff_id: handoffId,
      ...(partial ? { accepted_task_ids: selected } : {}),
    });
    assert.deepEqual(reply.result.task_ids, selected);
    const ws = c.workspaces.get("w")!;
    for (const tid of taskIds) {
      assert.equal(ws.tasks.get(tid)!.assignee, selected.includes(tid) ? "human:bob" : "human:alice");
    }
    assert.deepEqual(ws.handoffs.get(handoffId)!.accepted_task_ids, selected);
  });
}
