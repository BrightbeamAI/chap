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

test("task.update cannot complete a task awaiting review", () => {
  const { c, send, tid } = setup();
  send("review.request", { workspace: "wsp_r", from: "agent:bot",
    to: "human:alice", task_id: tid, artefact: { text: "draft" } });

  // The drafter tries to self-complete the task under review, with no reviewer
  // decision. The review gate lives in decide.*/abstain (reviewer-gated); a plain
  // task.update by a member must not reach a terminal state around it.
  const bypass = send("task.update", { workspace: "wsp_r", from: "agent:bot",
    task_id: tid, state: "completed" });
  assert.ok(bypass.error, "task.update must not complete a task awaiting review");
  assert.equal(c.workspaces.get("wsp_r")!.tasks.get(tid)!.state, "review_requested");

  // Withdrawing the review request back to in_progress stays legal.
  const withdraw = send("task.update", { workspace: "wsp_r", from: "agent:bot",
    task_id: tid, state: "in_progress" });
  assert.equal((withdraw.result as { state: string }).state, "in_progress");
});

function ruleSetup() {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const send = (m: string, p: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: `t-${m}`, method: m, params: p });
  send("workspace.create", { workspace: "w" });
  for (const who of ["human:a", "human:b", "human:c"])
    send("participant.join", { workspace: "w", from: who, type: "human", role: "reviewer" });
  send("participant.join", { workspace: "w", from: "agent:bot", type: "agent", role: "drafter" });
  const tid = (send("task.create", { workspace: "w", from: "human:a", kind: "draft",
    input: {}, assignee: "agent:bot" }).result as { task_id: string }).task_id;
  const st = (r: ReturnType<Coordinator["dispatch"]>) => (r.result as { state: string }).state;
  return { c, send, tid, st };
}

test("all_approve requires every named reviewer to approve", () => {
  const { send, tid, st } = ruleSetup();
  send("review.request", { workspace: "w", from: "agent:bot",
    to: ["human:a", "human:b", "human:c"], task_id: tid, artefact: { x: 1 }, rule: "all_approve" });
  assert.equal(st(send("decide.approve", { workspace: "w", from: "human:a", task_id: tid })), "review_requested");
  assert.equal(st(send("decide.approve", { workspace: "w", from: "human:b", task_id: tid })), "review_requested");
  assert.equal(st(send("decide.approve", { workspace: "w", from: "human:c", task_id: tid })), "completed");
});

test("all_approve ignores a duplicate approval from the same reviewer", () => {
  const { send, tid, st } = ruleSetup();
  send("review.request", { workspace: "w", from: "agent:bot",
    to: ["human:a", "human:b"], task_id: tid, artefact: { x: 1 }, rule: "all_approve" });
  send("decide.approve", { workspace: "w", from: "human:a", task_id: tid });
  assert.equal(st(send("decide.approve", { workspace: "w", from: "human:a", task_id: tid })), "review_requested");
  assert.equal(st(send("decide.approve", { workspace: "w", from: "human:b", task_id: tid })), "completed");
});

test("quorum completes at N distinct approvers", () => {
  const { send, tid, st } = ruleSetup();
  send("review.request", { workspace: "w", from: "agent:bot",
    to: ["human:a", "human:b", "human:c"], task_id: tid, artefact: { x: 1 }, rule: "quorum:2" });
  assert.equal(st(send("decide.approve", { workspace: "w", from: "human:a", task_id: tid })), "review_requested");
  assert.equal(st(send("decide.approve", { workspace: "w", from: "human:b", task_id: tid })), "completed");
});

test("review.request rejects a rule review/1.0 cannot honor", () => {
  const { send, tid } = ruleSetup();
  const r = send("review.request", { workspace: "w", from: "agent:bot",
    to: ["human:a"], task_id: tid, artefact: { x: 1 }, rule: "weighted_vote:2.0" });
  assert.equal(r.error?.code, -32602);
});
