/**
 * Regression: decision tags must be a list of strings, per the schema
 * (schemas/core/chap-task.schema.json). A non-string tag is rejected with PARAMS.
 * Guards the 0.2.9 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";
import { E } from "../src/jsonrpc.ts";

function readyReview() {
  const c = new Coordinator({ deterministicIds: true });
  const s = (method: string, params: unknown): any =>
    c.dispatch({ jsonrpc: "2.0", id: method, method, params } as never);
  s("workspace.create", { workspace: "w" });
  s("participant.join", { workspace: "w", from: "agent:b", type: "agent" });
  s("participant.join", { workspace: "w", from: "human:a", type: "human" });
  const tid = s("task.create", { workspace: "w", from: "human:a", kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  s("review.request", { workspace: "w", from: "agent:b", task_id: tid, to: ["human:a"], artefact: { v: 1 } });
  return { s, tid };
}

test("decide rejects non-string tags", () => {
  const { s, tid } = readyReview();
  const r = s("decide.approve", { workspace: "w", from: "human:a", task_id: tid, tags: [{ x: 1 }, 123] });
  assert.equal(r.error.code, E.PARAMS);
});

test("override rejects non-string tags", () => {
  const { s, tid } = readyReview();
  const r = s("decide.override", {
    workspace: "w", from: "human:a", task_id: tid,
    diff: [{ op: "replace", path: "/v", value: 2 }], rationale: "x", tags: ["ok", 5],
  });
  assert.equal(r.error.code, E.PARAMS);
});

test("string tags are accepted", () => {
  const { s, tid } = readyReview();
  const r = s("decide.approve", { workspace: "w", from: "human:a", task_id: tid, tags: ["routine"] });
  assert.equal(r.result.state, "completed");
});
