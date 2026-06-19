/**
 * End-to-end composition test: exercise every method handler from
 * every profile in one workspace sequence.
 *
 * Mirrors packages/coordinator-py/tests/test_end_to_end.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";

function makeCoord(): Coordinator {
  return new Coordinator({
    deterministicIds: true, deterministicClock: true,
    defaultProfiles: [
      "core/1.0", "review/1.0", "whisper/1.0",
      "deliberation/1.0", "handoff/1.0", "control/1.0",
      "routing/1.0", "audit-scitt/1.0",
    ],
  });
}

test("full lifecycle exercises every profile in one sequence", () => {
  const c = makeCoord();
  const send = (m: string, p: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: `e2e-${m}`, method: m, params: p });

  // workspace + participants
  const wsR = send("workspace.create", { workspace: "wsp_e2e" });
  assert.equal((wsR.result as { workspace: string }).workspace, "wsp_e2e");

  for (const [who, role] of [
    ["human:alice", "owner"],
    ["human:bob",   "reviewer"],
    ["human:carol", "reviewer"],
    ["agent:bot",   "drafter"],
  ] as const) {
    const r = send("participant.join", { workspace: "wsp_e2e", from: who,
      type: who.startsWith("human") ? "human" : "agent", role });
    assert.ok("result" in r && !r.error);
  }

  // task + update
  const tR = send("task.create", { workspace: "wsp_e2e", from: "human:alice",
    kind: "draft", input: { q: "?" }, assignee: "agent:bot",
    routing_hints: { criticality: "low", confidence: 0.9 }});
  const tid = (tR.result as { task_id: string }).task_id;
  send("task.update", { workspace: "wsp_e2e", task_id: tid, state: "in_progress", from: "agent:bot" });

  // whisper
  const wR = send("whisper.ask", { workspace: "wsp_e2e", from: "agent:bot",
    to: ["human:alice"], task_id: tid, question: "proceed?",
    options: [{ id: "yes" }, { id: "no" }],
    deadline_ms: 30000, default_if_lapsed: "no" });
  const wid = (wR.result as { whisper_id: string }).whisper_id;
  const wAns = send("whisper.answer", { workspace: "wsp_e2e", from: "human:alice",
    whisper_id: wid, answer_option: "yes" });
  assert.equal((wAns.result as { answered: boolean }).answered, true);

  // routing
  const depthR = send("review.depth", { workspace: "wsp_e2e", task_id: tid });
  assert.ok(["skip", "spot_check", "full"].includes((depthR.result as { depth: string }).depth));
  const escR = send("escalate.auto", { workspace: "wsp_e2e", task_id: tid,
    default_escalation_target: "human:bob" });
  assert.ok(typeof (escR.result as { escalate: boolean }).escalate === "boolean");

  // review.request + decide.override
  const draft = { comments: [{ severity: "warning", text: "x" }] };
  send("review.request", { workspace: "wsp_e2e", from: "agent:bot",
    to: "human:alice", task_id: tid, artefact: draft });
  const ovR = send("decide.override", { workspace: "wsp_e2e", from: "human:alice",
    task_id: tid,
    diff: [{ op: "replace", path: "/comments/0/severity", value: "info" }],
    rationale: "false positive", tags: ["false-positive"], intent_preserved: true });
  assert.equal((ovR.result as { applied: { comments: Array<{ severity: string }> }}).applied.comments[0].severity, "info");

  // abstain on a second task
  const t2R = send("task.create", { workspace: "wsp_e2e", from: "human:alice",
    kind: "review", input: {}, assignee: "agent:bot" });
  const tid2 = (t2R.result as { task_id: string }).task_id;
  send("task.update", { workspace: "wsp_e2e", task_id: tid2,
    state: "in_progress", from: "agent:bot" });
  send("review.request", { workspace: "wsp_e2e", from: "agent:bot",
    to: "human:bob", task_id: tid2, artefact: { x: 1 }});
  const abR = send("abstain.declare", { workspace: "wsp_e2e", from: "human:bob",
    task_id: tid2, reason: "conflict", category: "conflict_of_interest" });
  assert.equal((abR.result as { state: string }).state, "abstained");

  // deliberation
  const t3R = send("task.create", { workspace: "wsp_e2e", from: "human:alice",
    kind: "decide", input: {}, assignee: "human:alice" });
  const tid3 = (t3R.result as { task_id: string }).task_id;
  const dR = send("deliberate.open", { workspace: "wsp_e2e", from: "human:alice",
    to: ["human:alice", "human:bob", "human:carol"], task_id: tid3,
    rule: "quorum:2", question: "ship?" });
  const did = (dR.result as { deliberation_id: string }).deliberation_id;
  send("deliberate.comment", { workspace: "wsp_e2e", from: "human:alice",
    deliberation_id: did, comment: "risk is small" });
  send("deliberate.vote", { workspace: "wsp_e2e", from: "human:alice",
    deliberation_id: did, vote: "yea" });
  send("deliberate.vote", { workspace: "wsp_e2e", from: "human:bob",
    deliberation_id: did, vote: "yea" });
  const dClose = send("deliberate.close", { workspace: "wsp_e2e", from: "human:alice",
    deliberation_id: did });
  assert.equal((dClose.result as { outcome: string }).outcome, "approved");

  // handoff
  const t4R = send("task.create", { workspace: "wsp_e2e", from: "human:alice",
    kind: "shift", input: {}, assignee: "human:alice" });
  const tid4 = (t4R.result as { task_id: string }).task_id;
  const hR = send("handoff.propose", { workspace: "wsp_e2e", from: "human:alice",
    to: "human:bob", tasks: [{ task_id: tid4, title: "Open ticket" }],
    summary: "EOD handoff" });
  const hid = (hR.result as { handoff_id: string }).handoff_id;
  const hA = send("handoff.accept", { workspace: "wsp_e2e", from: "human:bob",
    handoff_id: hid });
  assert.equal((hA.result as { accepted: boolean }).accepted, true);

  // control snapshot + supersede + rollback
  const snapR = send("control.snapshot", { workspace: "wsp_e2e", from: "human:alice",
    label: "mid-test" });
  const snap = (snapR.result as { snapshot_artefact_id: string }).snapshot_artefact_id;

  const t5R = send("task.create", { workspace: "wsp_e2e", from: "human:alice",
    kind: "bad", input: {}, assignee: "agent:bot" });
  const tid5 = (t5R.result as { task_id: string }).task_id;
  const supR = send("control.supersede", { workspace: "wsp_e2e", from: "human:alice",
    task_id: tid5,
    successor_task: { kind: "redo", assignee: "agent:bot", input: { redo: true }},
    reason: "quality concern" });
  assert.equal((supR.result as { superseded_task_id: string }).superseded_task_id, tid5);

  const rbR = send("control.rollback", { workspace: "wsp_e2e", from: "human:alice",
    to_snapshot_artefact_id: snap, what_to_restore: ["mode_ceiling"] });
  assert.equal((rbR.result as { rolled_back_to: string }).rolled_back_to, snap);

  // control.pause/resume on participant scope
  send("control.pause", { workspace: "wsp_e2e", scope: "participant",
    participant_uri: "agent:bot", from: "human:alice" });
  send("control.resume", { workspace: "wsp_e2e", scope: "participant",
    participant_uri: "agent:bot", from: "human:alice" });

  // mode ceiling
  const mc = send("control.set_mode_ceiling", { workspace: "wsp_e2e",
    from: "human:alice", new_ceiling: "production" });
  assert.equal((mc.result as { mode_ceiling: string }).mode_ceiling, "production");

  // audit
  const vc = send("audit.verify_chain", { workspace: "wsp_e2e" });
  assert.equal((vc.result as { ok: boolean }).ok, true);
  const ss = send("audit.submit_to_scitt", { workspace: "wsp_e2e", from: "service:coordinator" });
  assert.ok(Array.isArray((ss.result as { statements: unknown[] }).statements));

  // describe
  const desc = send("workspace.describe", { workspace: "wsp_e2e" });
  const ws = desc.result as { task_count: number; override_count: number;
    audit_count: number; evidence_head: string };
  assert.ok(ws.task_count >= 5);
  assert.ok(ws.override_count >= 1);
  assert.ok(ws.audit_count > 30);
  assert.ok(ws.evidence_head?.startsWith("sha256:"));
});
