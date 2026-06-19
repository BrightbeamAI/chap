import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

function setup() {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const send = (m: string, p: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: `t-${m}`, method: m, params: p });
  send("workspace.create", { workspace: "wsp_r" });
  send("participant.join", { workspace: "wsp_r", from: "human:alice", type: "human", role: "reviewer" });
  send("participant.join", { workspace: "wsp_r", from: "agent:bot", type: "agent", role: "drafter" });
  const r = send("task.create", { workspace: "wsp_r", from: "human:alice",
    kind: "draft", input: {}, assignee: "agent:bot" });
  return { c, send, tid: (r.result as { task_id: string }).task_id };
}

test("decide.override applies a JSON Patch", () => {
  const { send, tid } = setup();
  const draft = { severity: "warning", text: "issue" };
  send("review.request", { workspace: "wsp_r", from: "agent:bot",
    to: "human:alice", task_id: tid, artefact: draft });
  const r = send("decide.override", {
    workspace: "wsp_r", from: "human:alice", task_id: tid,
    diff: [{ op: "replace", path: "/severity", value: "info" }],
    rationale: "false positive", tags: ["false-positive"],
  });
  assert.ok("result" in r && !r.error);
  assert.equal((r.result as { applied: { severity: string } }).applied.severity, "info");
});

test("decide.override carries intent_preserved and logical_id", () => {
  const { c, send, tid } = setup();
  send("review.request", { workspace: "wsp_r", from: "agent:bot",
    to: "human:alice", task_id: tid, artefact: { severity: "warning" } });
  const r = send("decide.override", {
    workspace: "wsp_r", from: "human:alice", task_id: tid,
    diff: [{ op: "replace", path: "/severity", value: "info" }],
    rationale: "cosmetic", tags: [], intent_preserved: true,
    logical_id: "lgl_abc123",
  });
  const artId = (r.result as { override_artefact_id: string }).override_artefact_id;
  const ws = c.workspaces.get("wsp_r")!;
  const override = ws.overrides.get(artId)!;
  assert.equal(override.intent_preserved, true);
  assert.equal(override.logical_id, "lgl_abc123");
});

test("decide.override rejects invalid JSON Patch with -32012", () => {
  const { send, tid } = setup();
  send("review.request", { workspace: "wsp_r", from: "agent:bot",
    to: "human:alice", task_id: tid, artefact: { a: 1 } });
  const r = send("decide.override", {
    workspace: "wsp_r", from: "human:alice", task_id: tid,
    diff: [{ op: "replace", path: "/nonexistent", value: 2 }],
    rationale: "x", tags: [],
  });
  assert.equal(r.error?.code, -32012);
});

test("decide.override requires review_requested state with -32010", () => {
  const { send, tid } = setup();
  const r = send("decide.override", {
    workspace: "wsp_r", from: "human:alice", task_id: tid,
    diff: [], rationale: "x", tags: [],
  });
  assert.equal(r.error?.code, -32010);
});

test("abstain.declare", () => {
  const { send, tid } = setup();
  send("review.request", { workspace: "wsp_r", from: "agent:bot",
    to: "human:alice", task_id: tid, artefact: {} });
  const r = send("abstain.declare", {
    workspace: "wsp_r", from: "human:alice", task_id: tid,
    reason: "conflict of interest", category: "conflict_of_interest",
  });
  assert.equal((r.result as { state: string }).state, "abstained");
});

test("escalate.raise creates a new task that supersedes the original", () => {
  const { send, tid } = setup();
  send("participant.join", { workspace: "wsp_r", from: "human:senior", type: "human", role: "lead" });
  const r = send("escalate.raise", {
    workspace: "wsp_r", from: "human:alice",
    original_task_id: tid,
    new_task: { kind: "review", input: { reason: "high-risk" }, assignee: "human:senior" },
  });
  assert.ok("result" in r && !r.error);
  assert.equal((r.result as { escalated_from: string }).escalated_from, tid);
});
