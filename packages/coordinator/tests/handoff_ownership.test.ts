/**
 * handoff.accept takes only tasks the proposer still holds (29 October
 * review). A task handed elsewhere after the proposal was moved again by
 * the accept, away from whoever held it. Mirrors test_handoff_ownership.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/index.js";

function ready() {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const send = (method: string, params: Record<string, unknown> = {}, from = "human:alice"): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params: { workspace: "w", from, ...params } });
  send("workspace.create", { profiles: ["core/1.0", "review/1.0", "handoff/1.0"] });
  for (const who of ["human:alice", "human:bob", "human:carol"]) send("participant.join", { type: "human" }, who);
  const ids: string[] = Array.from({ length: 2 }, () =>
    send("task.create", { kind: "k", input: {}, assignee: "human:alice" }).result.task_id);
  const toBob = send("handoff.propose", { to: "human:bob", tasks: ids.map((task_id) => ({ task_id })) }).result.handoff_id;
  const toCarol = send("handoff.propose", { to: "human:carol", tasks: [{ task_id: ids[0] }] }).result.handoff_id;
  return { c, send, ids, toBob, toCarol };
}

test("a task the proposer no longer holds is refused at accept, and nothing changes", () => {
  const { c, send, ids, toBob, toCarol } = ready();
  assert.equal(send("handoff.accept", { handoff_id: toCarol }, "human:carol").error, undefined);
  const before = structuredClone(c.snapshot());
  const r = send("handoff.accept", { handoff_id: toBob }, "human:bob");
  assert.deepEqual(r.error, { code: -32050, message: `Task ${ids[0]} is not currently assigned to human:alice` });
  assert.equal(c.workspaces.get("w")!.tasks.get(ids[0])!.assignee, "human:carol");
  // The refusal is recorded, as a member's refused call is; nothing else changes.
  const after = structuredClone(c.snapshot()) as Array<{ audit: unknown[] }>;
  after[0].audit.pop();
  assert.deepEqual(after, before);
});

test("the tasks the proposer still holds can be accepted alone", () => {
  const { c, send, ids, toBob, toCarol } = ready();
  send("handoff.accept", { handoff_id: toCarol }, "human:carol");
  const r = send("handoff.accept", { handoff_id: toBob, accepted_task_ids: [ids[1]] }, "human:bob");
  assert.deepEqual(r.result.task_ids, [ids[1]]);
  assert.equal(c.workspaces.get("w")!.tasks.get(ids[1])!.assignee, "human:bob");
});
