/**
 * Who may be given work, and who may move it.
 *
 * escalate.raise creates a task, so it meets the checks task.create and
 * control.supersede apply: the successor keeps the review requirement of the
 * original, a trial successor requires review under modes/1.0, a paused
 * assignee is refused and so is a mode above the current ceiling.
 * control.supersede keeps the review requirement too. The routing methods and
 * participant.leave are held to the membership floor (SPECIFICATION 6.3.1). A
 * paused participant is assigned no task through task.route, escalate.raise or
 * handoff.accept.
 *
 * Mirrors packages/coordinator-py/tests/test_assignment_guards.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";
import type { CoordinatorOptions } from "../src/index.js";

const WS = "wsp_guard";

function setup(profiles: string[], options: Partial<CoordinatorOptions> = {}) {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true, ...options });
  let n = 0;
  const send = (method: string, params: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: `g-${++n}`, method, params });
  send("workspace.create", { workspace: WS, profiles: ["core/1.0", "review/1.0", ...profiles] });
  for (const [uri, type, role] of [
    ["human:alice", "human", "admin"],
    ["human:bob", "human", "reviewer"],
    ["agent:bot", "agent", "drafter"],
    ["agent:senior", "agent", "drafter"],
  ]) {
    send("participant.join", { workspace: WS, from: uri, type, role });
  }
  const ws = c.workspaces.get(WS)!;
  return { c, send, ws };
}

type Send = ReturnType<typeof setup>["send"];

function createTask(send: Send, extra: Record<string, unknown> = {}): string {
  const r = send("task.create", {
    workspace: WS, from: "human:alice", kind: "k", input: {}, assignee: "agent:bot", ...extra,
  });
  assert.ok(r.result, `task.create failed: ${JSON.stringify(r.error)}`);
  const id = (r.result as { task_id: string }).task_id;
  send("task.update", { workspace: WS, from: "agent:bot", task_id: id, state: "in_progress" });
  return id;
}

function escalate(send: Send, original: string, assignee = "agent:senior", extra: Record<string, unknown> = {}) {
  return send("escalate.raise", {
    workspace: WS, from: "agent:bot", original_task_id: original,
    new_task: { kind: "k", assignee, input: {}, ...extra },
  });
}

function completeSuccessor(send: Send, successor: string): string {
  send("task.update", { workspace: WS, from: "agent:senior", task_id: successor, state: "in_progress" });
  const done = send("task.complete", {
    workspace: WS, from: "agent:senior", task_id: successor, output: { d: 1 },
  });
  assert.ok(done.result, `task.complete failed: ${JSON.stringify(done.error)}`);
  return (done.result as { state: string }).state;
}

function pause(send: Send, uri: string): void {
  const r = send("control.pause", { workspace: WS, from: "human:alice", scope: "participant", participant_uri: uri });
  assert.ok(r.result, `control.pause failed: ${JSON.stringify(r.error)}`);
}

// -------- escalate.raise --------

test("escalate.raise keeps the review the original required", () => {
  const { send, ws } = setup([]);
  const original = createTask(send, { review_required: true });

  const r = escalate(send, original);
  const successor = (r.result as { new_task_id: string }).new_task_id;

  assert.equal(ws.tasks.get(successor)!.review_required, true);
  assert.equal(completeSuccessor(send, successor), "review_requested",
    "the successor of a reviewed task must not complete directly");
});

test("escalate.raise forces review on a trial successor under modes/1.0", () => {
  // The original is a trial task made before the workspace advertised
  // modes/1.0, so it carries no review. Escalating it once the profile is on
  // makes a trial task, which requires review as one made by task.create does.
  const { send, ws } = setup(["control/1.0"]);
  const original = createTask(send, { mode: "trial" });
  assert.equal(ws.tasks.get(original)!.review_required, undefined);
  const set = send("workspace.set_profiles", {
    workspace: WS, from: "human:alice",
    profiles: ["core/1.0", "review/1.0", "control/1.0", "modes/1.0"],
  });
  assert.ok(set.result, `workspace.set_profiles failed: ${JSON.stringify(set.error)}`);

  const r = escalate(send, original);
  const successor = (r.result as { new_task_id: string }).new_task_id;

  assert.equal(ws.tasks.get(successor)!.mode, "trial");
  assert.equal(ws.tasks.get(successor)!.review_required, true);
  assert.equal(completeSuccessor(send, successor), "review_requested");
});

test("escalate.raise leaves review off when neither rule applies", () => {
  // Without modes/1.0 a trial mode is inert, as at task.create.
  const { send, ws } = setup([]);
  const original = createTask(send, { mode: "trial" });

  const r = escalate(send, original);
  const successor = (r.result as { new_task_id: string }).new_task_id;

  assert.equal(ws.tasks.get(successor)!.review_required, undefined);
  assert.equal(completeSuccessor(send, successor), "completed");
});

test("escalate.raise refuses a paused assignee", () => {
  const { send, ws } = setup(["control/1.0"]);
  const original = createTask(send);
  pause(send, "agent:senior");
  const tasksBefore = ws.tasks.size;

  const r = escalate(send, original);

  assert.equal(r.error?.code, -32063);
  assert.equal(r.error?.message, "Assignee agent:senior is paused");
  assert.equal(ws.tasks.size, tasksBefore);
  assert.equal(ws.tasks.get(original)!.state, "in_progress");
  // A member's refused call is recorded as a refusal (SPECIFICATION 10.1).
  assert.deepEqual(ws.audit[ws.audit.length - 1].outcome, { status: "refused", code: -32063 });
});

test("escalate.raise refuses a mode above the current ceiling", () => {
  const { send, ws } = setup(["control/1.0"]);
  const original = createTask(send, { mode: "production" });
  const lowered = send("control.set_mode_ceiling", { workspace: WS, from: "human:alice", new_ceiling: "trial" });
  assert.ok(lowered.result, `control.set_mode_ceiling failed: ${JSON.stringify(lowered.error)}`);
  const tasksBefore = ws.tasks.size;

  const r = escalate(send, original);

  assert.equal(r.error?.code, -32040);
  assert.equal(r.error?.message, "Requested mode production exceeds ceiling trial");
  assert.equal(ws.tasks.size, tasksBefore);
  assert.equal(ws.tasks.get(original)!.state, "in_progress");
});

test("escalate.raise takes a mode within a lowered ceiling from new_task", () => {
  const { send, ws } = setup(["control/1.0"]);
  const original = createTask(send, { mode: "production" });
  send("control.set_mode_ceiling", { workspace: WS, from: "human:alice", new_ceiling: "trial" });

  const above = escalate(send, original, "agent:senior", { mode: "production" });
  assert.equal(above.error?.code, -32040);
  const malformed = escalate(send, original, "agent:senior", { mode: 3 });
  assert.equal(malformed.error?.code, -32602);
  assert.equal(malformed.error?.message, "new_task.mode must be a string");

  const r = escalate(send, original, "agent:senior", { mode: "trial" });
  const successor = (r.result as { new_task_id: string }).new_task_id;
  assert.equal(ws.tasks.get(successor)!.mode, "trial");
  assert.equal(ws.tasks.get(original)!.state, "escalated");
});

test("control.supersede keeps the review requirement of the task it replaces", () => {
  // The successor's own review_required: false cannot remove it.
  const { send, ws } = setup(["control/1.0"]);
  const original = createTask(send, { review_required: true });

  const r = send("control.supersede", {
    workspace: WS, from: "agent:bot", task_id: original, reason: "redo",
    successor_task: { kind: "v2", assignee: "agent:senior", input: {}, review_required: false },
  });
  const successor = (r.result as { new_task_id: string }).new_task_id;

  assert.equal(ws.tasks.get(successor)!.review_required, true);
  assert.equal(completeSuccessor(send, successor), "review_requested");
});

test("control.supersede still lets a successor of an unreviewed task opt out", () => {
  const { send, ws } = setup(["control/1.0"]);
  const original = createTask(send);

  const r = send("control.supersede", {
    workspace: WS, from: "human:alice", task_id: original, reason: "redo",
    successor_task: { kind: "v2", assignee: "agent:senior", input: {}, review_required: false },
  });
  const successor = (r.result as { new_task_id: string }).new_task_id;

  assert.equal(ws.tasks.get(successor)!.review_required, false);
  assert.equal(completeSuccessor(send, successor), "completed");
});

// -------- membership --------

const ROUTING_CALLS: [string, Record<string, unknown>][] = [
  ["task.route", { candidates: ["human:bob"] }],
  ["review.depth", {}],
  ["escalate.auto", { default_escalation_target: "human:bob" }],
];

test("a non-member cannot call the routing methods", () => {
  const { send, ws } = setup(["routing/1.0"]);
  const tid = createTask(send, { routing_hints: { criticality: "critical" } });

  for (const [method, extra] of ROUTING_CALLS) {
    const auditBefore = ws.audit.length;
    const decisionsBefore = ws.route_decisions.size;
    const r = send(method, { workspace: WS, from: "human:outsider", task_id: tid, ...extra });
    assert.equal(r.error?.code, -32011, `${method} from a non-member`);
    // A non-member's refusal is never recorded.
    assert.equal(ws.audit.length, auditBefore, `${method} wrote to the log`);
    assert.equal(ws.route_decisions.size, decisionsBefore, `${method} recorded a decision`);
    assert.equal(ws.tasks.get(tid)!.assignee, "agent:bot", `${method} moved the task`);
  }

  // The same calls from a member succeed.
  for (const [method, extra] of ROUTING_CALLS) {
    const r = send(method, { workspace: WS, from: "human:alice", task_id: tid, ...extra });
    assert.ok(r.result, `${method} from a member failed: ${JSON.stringify(r.error)}`);
  }
  assert.equal(ws.tasks.get(tid)!.assignee, "human:bob");
});

test("a non-member cannot write a leave to the log", () => {
  const { send, ws } = setup([]);
  const auditBefore = ws.audit.length;

  const outsider = send("participant.leave", { workspace: WS, from: "human:outsider" });
  assert.equal(outsider.error?.code, -32011);
  assert.equal(ws.audit.length, auditBefore);

  const member = send("participant.leave", { workspace: WS, from: "agent:senior" });
  assert.deepEqual(member.result, { left: true });
  assert.equal(ws.members.has("agent:senior"), false);
  assert.equal(ws.audit.length, auditBefore + 1);

  const again = send("participant.leave", { workspace: WS, from: "agent:senior" });
  assert.equal(again.error?.code, -32011);
  assert.equal(ws.audit.length, auditBefore + 1);
});

test("a from that is not a string is refused in fixed words", () => {
  const { send, ws } = setup(["routing/1.0"]);
  const tid = createTask(send);
  const auditBefore = ws.audit.length;

  for (const from of [undefined, null, ["human:alice"], { uri: "human:alice" }, 7, true]) {
    for (const [method, extra] of [
      ["participant.leave", {}],
      ["task.route", { task_id: tid, candidates: ["human:bob"] }],
    ] as [string, Record<string, unknown>][]) {
      const params: Record<string, unknown> = { workspace: WS, ...extra };
      if (from !== undefined) params.from = from;
      const r = send(method, params);
      assert.equal(r.error?.code, -32011, `${method} from ${JSON.stringify(from)}`);
      assert.equal(r.error?.message, "Not a workspace member: from is not a participant URI");
    }
  }
  assert.equal(ws.audit.length, auditBefore);
  assert.equal(ws.members.size, 4);
});

test("a from or workspace that is not a string is refused under signatures and step-up", () => {
  for (const options of [{ requireSignatures: true }, { enforceStepUp: true }] as Partial<CoordinatorOptions>[]) {
    const c = new Coordinator({ deterministicIds: true, deterministicClock: true, ...options });
    const send = (method: string, params: Record<string, unknown>, sig?: string) =>
      c.dispatch({ jsonrpc: "2.0", id: method, method, params, ...(sig ? { sig } : {}) } as never);
    send("workspace.create", { workspace: WS, profiles: ["core/1.0", "control/1.0"] });
    send("participant.join", { workspace: WS, from: "human:alice", type: "human", role: "admin" });
    const sig = options.requireSignatures ? "ed25519:k1:AAAA" : undefined;
    const expected = options.requireSignatures
      ? { code: -32070, message: "Cannot verify signature: missing from/workspace" }
      : { code: -32011, message: "Not a workspace member: from is not a participant URI" };
    for (const params of [
      { workspace: WS, from: ["human:alice"] },
      { workspace: WS, from: { uri: "human:alice" } },
      ...(options.requireSignatures ? [{ workspace: [WS], from: "human:alice" }] : []),
    ]) {
      const method = options.requireSignatures ? "participant.leave" : "control.pause";
      const r = send(method, { ...params, scope: "workspace" }, sig);
      assert.equal(r.error?.code, expected.code, `${JSON.stringify(options)} ${JSON.stringify(params)}`);
      assert.equal(r.error?.message, expected.message);
    }
    assert.equal(c.workspaces.get(WS)!.state, "active");
    assert.equal(c.workspaces.get(WS)!.members.size, 1);
  }
});

test("malformed fields are refused in the same words by both references", () => {
  const { send, ws } = setup(["control/1.0", "routing/1.0"]);
  const tid = createTask(send);
  const cases: [string, Record<string, unknown>, string][] = [
    ["task.create", { kind: "k", input: {}, assignee: "agent:bot", review_required: [] }, "review_required must be a boolean"],
    ["task.create", { kind: "k", input: {}, assignee: "agent:bot", review_required: "yes" }, "review_required must be a boolean"],
    ["task.create", { kind: "k", input: {}, assignee: "agent:bot", mode: { m: 1 } }, "mode must be a string"],
    ["task.create", { kind: "k", input: {}, assignee: ["agent:bot"] }, "Assignee not in workspace"],
    ["control.supersede", { task_id: tid, successor_task: { kind: "v2", review_required: {} } }, "successor_task.review_required must be a boolean"],
    ["control.supersede", { task_id: tid, successor_task: { kind: "v2", mode: ["trial"] } }, "successor_task.mode must be a string"],
    ["control.supersede", { task_id: tid, successor_task: 5 }, "successor_task must include kind"],
    ["task.route", { task_id: tid, candidates: { "human:bob": 1 } }, "candidates must be a list"],
    ["task.route", { task_id: tid, candidates: "human:bob" }, "candidates must be a list"],
    ["escalate.raise", { original_task_id: tid, new_task: "agent:senior" }, "new_task must be an object"],
    ["escalate.raise", { original_task_id: [tid], new_task: { assignee: "agent:senior" } }, "Unknown original task"],
    ["escalate.raise", { original_task_id: tid, new_task: { assignee: ["agent:senior"] } }, "Escalation assignee not in workspace"],
  ];
  for (const [method, extra, message] of cases) {
    const r = send(method, { workspace: WS, from: "human:alice", ...extra });
    assert.equal(r.error?.code, -32602, `${method} ${JSON.stringify(extra)}`);
    assert.equal(r.error?.message, message, `${method} ${JSON.stringify(extra)}`);
  }
  assert.equal(ws.tasks.size, 1);
  assert.equal(ws.tasks.get(tid)!.state, "in_progress");
  assert.equal(ws.tasks.get(tid)!.assignee, "agent:bot");
});

test("an empty successor mode falls back to the original's in both successor methods", () => {
  const { send, ws } = setup(["control/1.0"]);
  const first = createTask(send, { mode: "shadow" });
  const r1 = send("control.supersede", {
    workspace: WS, from: "human:alice", task_id: first, reason: "redo",
    successor_task: { kind: "v2", assignee: "agent:senior", input: {}, mode: "" },
  });
  assert.equal(ws.tasks.get((r1.result as { new_task_id: string }).new_task_id)!.mode, "shadow");
  const second = createTask(send, { mode: "shadow" });
  const r2 = escalate(send, second, "agent:senior", { mode: "" });
  assert.equal(ws.tasks.get((r2.result as { new_task_id: string }).new_task_id)!.mode, "shadow");
});

// -------- participant pause --------

test("task.route passes over a paused candidate", () => {
  const { send, ws } = setup(["routing/1.0", "control/1.0"]);
  const tid = createTask(send);
  pause(send, "human:bob");

  const r = send("task.route", {
    workspace: WS, from: "human:alice", task_id: tid, candidates: ["human:bob", "human:alice"],
  });

  const out = r.result as { selected: string; rationale: { alternatives_considered: unknown[] } };
  assert.equal(out.selected, "human:alice");
  assert.deepEqual(out.rationale.alternatives_considered,
    [{ candidate: "human:bob", reason_excluded: "paused" }]);
  assert.equal(ws.tasks.get(tid)!.assignee, "human:alice");
});

test("task.route with no unpaused member among the candidates answers -32510", () => {
  const { send, ws } = setup(["routing/1.0", "control/1.0"]);
  const tid = createTask(send);
  pause(send, "human:bob");

  const r = send("task.route", {
    workspace: WS, from: "human:alice", task_id: tid, candidates: ["human:bob", "human:ghost"],
  });

  assert.equal(r.error?.code, -32510);
  assert.equal(r.error?.message, "No candidate is a workspace member who is not paused");
  assert.equal(ws.tasks.get(tid)!.assignee, "agent:bot");
});

test("task.route refuses a paused participant an operator policy picks", () => {
  const { send, ws } = setup(["routing/1.0", "control/1.0"], {
    routingPolicy: () => ({ selected: "human:bob", rationale: { policy_id: "always-bob", summary: "bob" } }),
  });
  const tid = createTask(send);
  pause(send, "human:bob");
  const decisionsBefore = ws.route_decisions.size;

  const r = send("task.route", {
    workspace: WS, from: "human:alice", task_id: tid, candidates: ["human:bob", "human:alice"],
  });

  assert.equal(r.error?.code, -32063);
  assert.equal(r.error?.message, "Assignee human:bob is paused");
  assert.equal(ws.tasks.get(tid)!.assignee, "agent:bot");
  assert.equal(ws.route_decisions.size, decisionsBefore);
});

test("handoff.accept refuses a paused recipient until it is resumed", () => {
  const { send, ws } = setup(["handoff/1.0", "control/1.0"]);
  const tid = createTask(send);
  const proposed = send("handoff.propose", {
    workspace: WS, from: "agent:bot", to: "human:bob", tasks: [{ task_id: tid }],
  });
  const hid = (proposed.result as { handoff_id: string }).handoff_id;
  pause(send, "human:bob");

  const refused = send("handoff.accept", { workspace: WS, from: "human:bob", handoff_id: hid });
  assert.equal(refused.error?.code, -32063);
  assert.equal(refused.error?.message, "Acceptor human:bob is paused");
  assert.equal(ws.handoffs.get(hid)!.state, "proposed");
  assert.equal(ws.tasks.get(tid)!.assignee, "agent:bot");

  send("control.resume", { workspace: WS, from: "human:alice", scope: "participant", participant_uri: "human:bob" });
  const accepted = send("handoff.accept", { workspace: WS, from: "human:bob", handoff_id: hid });
  assert.ok(accepted.result, `handoff.accept failed: ${JSON.stringify(accepted.error)}`);
  assert.equal(ws.tasks.get(tid)!.assignee, "human:bob");
});

test("handoff.accept refuses a paused member of a group recipient", () => {
  const { send, ws } = setup(["handoff/1.0", "control/1.0"]);
  const tid = createTask(send);
  const proposed = send("handoff.propose", {
    workspace: WS, from: "agent:bot", to: "group:reviewers", tasks: [{ task_id: tid }],
  });
  const hid = (proposed.result as { handoff_id: string }).handoff_id;
  pause(send, "human:bob");

  const refused = send("handoff.accept", { workspace: WS, from: "human:bob", handoff_id: hid });
  assert.equal(refused.error?.code, -32063);

  const accepted = send("handoff.accept", { workspace: WS, from: "human:alice", handoff_id: hid });
  assert.ok(accepted.result, `handoff.accept failed: ${JSON.stringify(accepted.error)}`);
  assert.equal(ws.tasks.get(tid)!.assignee, "human:alice");
});
