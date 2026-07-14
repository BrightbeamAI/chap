/**
 * Tests for whisper, deliberation, handoff, control, routing, audit-scitt.
 * Mirrors packages/coordinator-py/tests/test_profiles.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

interface Ready {
  c: Coordinator;
  send: (m: string, p: Record<string, unknown>) => ReturnType<Coordinator["dispatch"]>;
  tid: string;
}

function setup(): Ready {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const send = (m: string, p: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: `t-${m}`, method: m, params: p });
  send("workspace.create", { workspace: "wsp_p", profiles: [
    "core/1.0", "review/1.0", "whisper/1.0", "deliberation/1.0",
    "handoff/1.0", "control/1.0", "routing/1.0", "audit-scitt/1.0",
  ]});
  send("participant.join", { workspace: "wsp_p", from: "human:alice", type: "human", role: "owner" });
  send("participant.join", { workspace: "wsp_p", from: "human:bob",   type: "human", role: "reviewer" });
  send("participant.join", { workspace: "wsp_p", from: "agent:bot",   type: "agent", role: "drafter" });
  const r = send("task.create", { workspace: "wsp_p", from: "human:alice",
    kind: "k", input: {}, assignee: "agent:bot" });
  return { c, send, tid: (r.result as { task_id: string }).task_id };
}

// -------- whisper/1.0 --------

test("whisper.ask + whisper.answer with answer_option", () => {
  const { send, tid } = setup();
  const r1 = send("whisper.ask", { workspace: "wsp_p", from: "agent:bot",
    to: ["human:alice"], task_id: tid, question: "confirm?",
    options: [{ id: "yes" }, { id: "no" }],
    deadline_ms: 30000, default_if_lapsed: "no" });
  assert.ok("result" in r1 && !r1.error);
  const wid = (r1.result as { whisper_id: string }).whisper_id;
  const r2 = send("whisper.answer", { workspace: "wsp_p", from: "human:alice",
    whisper_id: wid, answer_option: "yes" });
  assert.equal((r2.result as { answered: boolean }).answered, true);
  // Can't answer twice
  const r3 = send("whisper.answer", { workspace: "wsp_p", from: "human:alice",
    whisper_id: wid, answer_option: "no" });
  assert.equal(r3.error?.code, -32020);  // WHISPER_ALREADY_ANSWERED
});

test("whisper option-not-in-set returns -32022", () => {
  const { send, tid } = setup();
  const r1 = send("whisper.ask", { workspace: "wsp_p", from: "agent:bot",
    to: ["human:alice"], task_id: tid, question: "?",
    options: [{ id: "a" }, { id: "b" }],
    deadline_ms: 30000, default_if_lapsed: "a" });
  const wid = (r1.result as { whisper_id: string }).whisper_id;
  const r2 = send("whisper.answer", { workspace: "wsp_p", from: "human:alice",
    whisper_id: wid, answer_option: "c" });
  assert.equal(r2.error?.code, -32022);
});

test("whisper lapse emits notify.message and marks lapsed", () => {
  const { c, send, tid } = setup();
  const r = send("whisper.ask", { workspace: "wsp_p", from: "agent:bot",
    to: ["human:alice"], task_id: tid, question: "?",
    options: [{ id: "a" }], deadline_ms: 1, default_if_lapsed: "a" });
  const wid = (r.result as { whisper_id: string }).whisper_id;
  const emitted = c.checkWhisperLapses!("wsp_p", "2100-01-01T00:00:00.000Z");
  assert.equal(emitted.length, 1);
  // Answering now fails with WHISPER_LAPSED
  const r2 = send("whisper.answer", { workspace: "wsp_p", from: "human:alice",
    whisper_id: wid, answer_option: "a" });
  assert.equal(r2.error?.code, -32021);
});

// -------- deliberation/1.0 --------

test("deliberation any_one_approves", () => {
  const { send, tid } = setup();
  const r = send("deliberate.open", { workspace: "wsp_p", from: "human:alice",
    to: ["human:alice", "human:bob"], task_id: tid, rule: "any_one_approves" });
  const did = (r.result as { deliberation_id: string }).deliberation_id;
  send("deliberate.vote", { workspace: "wsp_p", from: "human:alice",
    deliberation_id: did, vote: "yea" });
  const r2 = send("deliberate.close", { workspace: "wsp_p", from: "human:alice",
    deliberation_id: did });
  assert.equal((r2.result as { outcome: string }).outcome, "approved");
});

test("deliberation quorum: only one vote -> rejected", () => {
  const { send, tid } = setup();
  const r = send("deliberate.open", { workspace: "wsp_p", from: "human:alice",
    to: ["human:alice", "human:bob", "agent:bot"], task_id: tid, rule: "quorum:2" });
  const did = (r.result as { deliberation_id: string }).deliberation_id;
  send("deliberate.vote", { workspace: "wsp_p", from: "human:alice",
    deliberation_id: did, vote: "yea" });
  const r2 = send("deliberate.close", { workspace: "wsp_p", from: "human:alice",
    deliberation_id: did });
  const outcome = r2.result as { outcome: string; reason?: string };
  assert.equal(outcome.outcome, "rejected");
  assert.equal(outcome.reason, "quorum not met");
});

test("deliberation veto blocks even with majority yea", () => {
  const { send, tid } = setup();
  const r = send("deliberate.open", { workspace: "wsp_p", from: "human:alice",
    to: ["human:alice", "human:bob"], task_id: tid,
    rule: "weighted_vote_with_veto:1.0",
    weights: { "human:alice": 1.0, "human:bob": 1.0 },
    veto: { "human:bob": true } });
  const did = (r.result as { deliberation_id: string }).deliberation_id;
  send("deliberate.vote", { workspace: "wsp_p", from: "human:alice",
    deliberation_id: did, vote: "yea" });
  send("deliberate.vote", { workspace: "wsp_p", from: "human:bob",
    deliberation_id: did, vote: "nay", veto_invoked: true });
  const r2 = send("deliberate.close", { workspace: "wsp_p", from: "human:alice",
    deliberation_id: did });
  const out = r2.result as { outcome: string; vetoes: string[] };
  assert.equal(out.outcome, "rejected");
  assert.ok(out.vetoes.includes("human:bob"));
});

test("deliberation: voter not in participant list -> -32030", () => {
  const { send, tid } = setup();
  const r = send("deliberate.open", { workspace: "wsp_p", from: "human:alice",
    to: ["human:alice"], task_id: tid, rule: "any_one_approves" });
  const did = (r.result as { deliberation_id: string }).deliberation_id;
  const r2 = send("deliberate.vote", { workspace: "wsp_p", from: "human:bob",
    deliberation_id: did, vote: "yea" });
  assert.equal(r2.error?.code, -32030);
});

test("deliberation: already voted -> -32031", () => {
  const { send, tid } = setup();
  const r = send("deliberate.open", { workspace: "wsp_p", from: "human:alice",
    to: ["human:alice", "human:bob"], task_id: tid, rule: "any_one_approves" });
  const did = (r.result as { deliberation_id: string }).deliberation_id;
  send("deliberate.vote", { workspace: "wsp_p", from: "human:alice",
    deliberation_id: did, vote: "yea" });
  const r2 = send("deliberate.vote", { workspace: "wsp_p", from: "human:alice",
    deliberation_id: did, vote: "nay" });
  assert.equal(r2.error?.code, -32031);
});

test("deliberation: unknown rule -> -32033", () => {
  const { send, tid } = setup();
  const r = send("deliberate.open", { workspace: "wsp_p", from: "human:alice",
    to: ["human:alice"], task_id: tid, rule: "made_up_rule" });
  assert.equal(r.error?.code, -32033);
});

// -------- handoff/1.0 --------

test("handoff propose + accept (single task)", () => {
  const { c, send, tid } = setup();
  // Reassign tid to alice so she can hand it off
  c.workspaces.get("wsp_p")!.tasks.get(tid)!.assignee = "human:alice";
  const r = send("handoff.propose", { workspace: "wsp_p",
    from: "human:alice", to: "human:bob",
    tasks: [{ task_id: tid, title: "shift" }], summary: "EOD" });
  assert.ok("result" in r && !r.error);
  const hid = (r.result as { handoff_id: string }).handoff_id;
  const r2 = send("handoff.accept", { workspace: "wsp_p", from: "human:bob",
    handoff_id: hid });
  assert.equal((r2.result as { accepted: boolean }).accepted, true);
  assert.equal((r2.result as { assignee: string }).assignee, "human:bob");
});

test("handoff: recipient not member -> -32052", () => {
  const { c, send, tid } = setup();
  c.workspaces.get("wsp_p")!.tasks.get(tid)!.assignee = "human:alice";
  const r = send("handoff.propose", { workspace: "wsp_p",
    from: "human:alice", to: "human:nobody",
    tasks: [{ task_id: tid }] });
  assert.equal(r.error?.code, -32052);
});

test("handoff: proposer doesn't own task -> -32050", () => {
  const { send, tid } = setup();
  // bob proposes a task assigned to bot
  const r = send("handoff.propose", { workspace: "wsp_p",
    from: "human:bob", to: "human:alice",
    tasks: [{ task_id: tid }] });
  assert.equal(r.error?.code, -32050);
});

test("handoff already resolved -> -32051", () => {
  const { c, send, tid } = setup();
  c.workspaces.get("wsp_p")!.tasks.get(tid)!.assignee = "human:alice";
  const r = send("handoff.propose", { workspace: "wsp_p",
    from: "human:alice", to: "human:bob",
    tasks: [{ task_id: tid }] });
  const hid = (r.result as { handoff_id: string }).handoff_id;
  send("handoff.accept", { workspace: "wsp_p", from: "human:bob", handoff_id: hid });
  const r2 = send("handoff.accept", { workspace: "wsp_p", from: "human:bob", handoff_id: hid });
  assert.equal(r2.error?.code, -32051);
});

test("handoff multi-task: reassigns all tasks atomically", () => {
  const { c, send, tid } = setup();
  const r = send("task.create", { workspace: "wsp_p", from: "human:alice",
    kind: "k", input: {}, assignee: "agent:bot" });
  const tid2 = (r.result as { task_id: string }).task_id;
  c.workspaces.get("wsp_p")!.tasks.get(tid)!.assignee = "human:alice";
  c.workspaces.get("wsp_p")!.tasks.get(tid2)!.assignee = "human:alice";
  const r2 = send("handoff.propose", { workspace: "wsp_p",
    from: "human:alice", to: "human:bob",
    tasks: [{ task_id: tid }, { task_id: tid2 }] });
  const hid = (r2.result as { handoff_id: string }).handoff_id;
  const r3 = send("handoff.accept", { workspace: "wsp_p", from: "human:bob", handoff_id: hid });
  const ids = (r3.result as { task_ids: string[] }).task_ids;
  assert.equal(ids.length, 2);
  assert.equal(c.workspaces.get("wsp_p")!.tasks.get(tid)!.assignee, "human:bob");
  assert.equal(c.workspaces.get("wsp_p")!.tasks.get(tid2)!.assignee, "human:bob");
});

// -------- control/1.0 --------

test("control.pause + control.resume at task scope", () => {
  const { send, tid } = setup();
  send("task.update", { workspace: "wsp_p", task_id: tid, state: "in_progress", from: "agent:bot" });
  const r1 = send("control.pause", { workspace: "wsp_p", scope: "task", task_id: tid, from: "human:alice" });
  assert.equal((r1.result as { state: string }).state, "paused");
  const r2 = send("control.resume", { workspace: "wsp_p", scope: "task", task_id: tid, from: "human:alice" });
  assert.equal((r2.result as { state: string }).state, "in_progress");
});

test("control.pause at participant scope", () => {
  const { send } = setup();
  const r = send("control.pause", { workspace: "wsp_p", scope: "participant",
    participant_uri: "agent:bot", from: "human:alice" });
  assert.equal((r.result as { paused: boolean }).paused, true);
});

test("control.pause at workspace scope blocks new work with -32063", () => {
  const { send } = setup();
  send("control.pause", { workspace: "wsp_p", scope: "workspace", from: "human:alice" });
  const r = send("task.create", { workspace: "wsp_p", from: "human:alice",
    kind: "k", input: {}, assignee: "agent:bot" });
  assert.equal(r.error?.code, -32063);
});

test("control.snapshot returns art_ id", () => {
  const { send } = setup();
  const r = send("control.snapshot", { workspace: "wsp_p", from: "human:alice", label: "before" });
  const out = r.result as { snapshot_artefact_id: string; artefact: { kind: string } };
  assert.ok(out.snapshot_artefact_id.startsWith("art_"));
  assert.equal(out.artefact.kind, "snapshot");
});

test("control.rollback uses to_snapshot_artefact_id", () => {
  const { send } = setup();
  const r1 = send("control.snapshot", { workspace: "wsp_p", from: "human:alice",
    label: "x", include: ["mode_ceiling"] });
  const snap = (r1.result as { snapshot_artefact_id: string }).snapshot_artefact_id;
  const r2 = send("control.rollback", { workspace: "wsp_p", from: "human:alice",
    to_snapshot_artefact_id: snap, what_to_restore: ["mode_ceiling"] });
  const out = r2.result as { rolled_back_to: string; restored: string[] };
  assert.equal(out.rolled_back_to, snap);
  assert.ok(out.restored.includes("mode_ceiling"));
});

test("control.supersede creates successor task", () => {
  const { c, send, tid } = setup();
  const r = send("control.supersede", { workspace: "wsp_p", from: "human:alice",
    task_id: tid,
    successor_task: { kind: "redo", assignee: "agent:bot", input: { redo: true } },
    reason: "buggy v1" });
  const newId = (r.result as { new_task_id: string }).new_task_id;
  assert.ok(c.workspaces.get("wsp_p")!.tasks.has(newId));
  assert.equal(c.workspaces.get("wsp_p")!.tasks.get(newId)!.supersedes, tid);
});

test("control.set_mode_ceiling blocks higher mode with -32040", () => {
  const { send } = setup();
  const r1 = send("control.set_mode_ceiling", { workspace: "wsp_p",
    from: "human:alice", new_ceiling: "trial" });
  assert.equal((r1.result as { mode_ceiling: string }).mode_ceiling, "trial");
  const r2 = send("task.create", { workspace: "wsp_p", from: "human:alice",
    kind: "k", input: {}, assignee: "agent:bot", mode: "production" });
  assert.equal(r2.error?.code, -32040);
});

// -------- routing/1.0 --------

test("review.depth: high criticality -> full", () => {
  const { c, send, tid } = setup();
  c.workspaces.get("wsp_p")!.tasks.get(tid)!.routing_hints = {
    criticality: "critical", confidence: "0.9",
  };
  const r = send("review.depth", { workspace: "wsp_p", task_id: tid });
  assert.equal((r.result as { depth: string }).depth, "full");
});

test("review.depth: low crit + high conf -> skip", () => {
  const { c, send, tid } = setup();
  c.workspaces.get("wsp_p")!.tasks.get(tid)!.routing_hints = {
    criticality: "low", confidence: "0.97",
  };
  const r = send("review.depth", { workspace: "wsp_p", task_id: tid });
  assert.equal((r.result as { depth: string }).depth, "skip");
});

test("review.depth: spot_check carries sampling_probability", () => {
  const { c, send, tid } = setup();
  c.workspaces.get("wsp_p")!.tasks.get(tid)!.routing_hints = {
    criticality: "low", confidence: "0.85",
  };
  const r = send("review.depth", { workspace: "wsp_p", task_id: tid });
  const out = r.result as { depth: string; sampling_probability: number };
  assert.equal(out.depth, "spot_check");
  assert.ok(out.sampling_probability > 0 && out.sampling_probability <= 1);
});

test("task.route picks an eligible candidate and updates assignee", () => {
  const { c, send, tid } = setup();
  const r = send("task.route", { workspace: "wsp_p", task_id: tid,
    candidates: ["nobody@nowhere", "human:bob"] });
  assert.equal((r.result as { selected: string }).selected, "human:bob");
  assert.equal(c.workspaces.get("wsp_p")!.tasks.get(tid)!.assignee, "human:bob");
  assert.ok((r.result as { decision_artefact: string }).decision_artefact.startsWith("art_"));
});

test("task.route: empty candidates -> -32513", () => {
  const { send, tid } = setup();
  const r = send("task.route", { workspace: "wsp_p", task_id: tid, candidates: [] });
  assert.equal(r.error?.code, -32513);
});

test("escalate.auto: critical triggers escalation", () => {
  const { c, send, tid } = setup();
  c.workspaces.get("wsp_p")!.tasks.get(tid)!.routing_hints = {
    criticality: "critical", confidence: "0.9",
  };
  const r = send("escalate.auto", { workspace: "wsp_p", task_id: tid,
    default_escalation_target: "human:bob" });
  const out = r.result as { escalate: boolean; to: string };
  assert.equal(out.escalate, true);
  assert.equal(out.to, "human:bob");
});

// -------- audit-scitt/1.0 --------

test("audit.verify_chain succeeds on a fresh chain", () => {
  const { send } = setup();
  const r = send("audit.verify_chain", { workspace: "wsp_p" });
  const out = r.result as { ok: boolean; entries_checked: number };
  assert.equal(out.ok, true);
  assert.ok(out.entries_checked > 0);
});

test("audit.submit_to_scitt without submitter returns statements", () => {
  const { send } = setup();
  const r = send("audit.submit_to_scitt", { workspace: "wsp_p", from: "service:coordinator" });
  const out = r.result as { statements: unknown[] };
  assert.ok(Array.isArray(out.statements));
  assert.ok(out.statements.length > 0);
});

test("audit.submit_to_scitt with submitter calls the hook", () => {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const captured: unknown[] = [];
  c.options.scittSubmitter = (s) => { captured.push(s); return { receipt_id: `r-${captured.length}` }; };
  const send = (m: string, p: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: `t-${m}`, method: m, params: p });
  send("workspace.create", { workspace: "wsp_p2", profiles: ["core/1.0", "audit-scitt/1.0"] });
  send("participant.join", { workspace: "wsp_p2", from: "human:alice", type: "human", role: "owner" });
  const r = send("audit.submit_to_scitt", { workspace: "wsp_p2", from: "service:coordinator" });
  assert.ok((r.result as { receipts: unknown[] }).receipts.length > 0);
});
