/**
 * review/1.0: binding a decision to the content it decided on.
 *
 * Two related gaps, reported against 9e7af2b by Iman Schrock (EMILIA) from the
 * CHAP-to-AEB interoperability profile, and tracked as #71 and #72.
 *
 * A plain `decide.approve` bound `task_id` and no content, so a relying party
 * could not tell what was approved. And `review.request` had no guard, so the
 * artefact under an open review could be replaced with the decision envelope
 * carrying nothing that would detect the swap.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "../src/index.js";
import { contentHash } from "../src/canonical.js";

const DRAFT = { severity: "warning", text: "the reviewed text" };
const SWAPPED = { severity: "critical", text: "something else entirely" };

function setup() {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  const send = (m: string, p: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: `t-${m}`, method: m, params: p }) as
      { result?: Record<string, unknown>; error?: { code: number; message: string; data?: unknown } };
  send("workspace.create", { workspace: "wsp_b" });
  for (const [from, role] of [["human:alice", "owner"], ["human:bob", "reviewer"],
                              ["human:carol", "reviewer"], ["agent:bot", "drafter"]]) {
    send("participant.join", { workspace: "wsp_b", from, role,
      type: from.startsWith("human:") ? "human" : "agent" });
  }
  const r = send("task.create", { workspace: "wsp_b", from: "human:alice",
    kind: "draft", input: {}, assignee: "agent:bot" });
  const tid = (r.result as { task_id: string }).task_id;
  send("task.update", { workspace: "wsp_b", from: "agent:bot", task_id: tid, state: "in_progress" });
  send("task.complete", { workspace: "wsp_b", from: "agent:bot", task_id: tid,
    output: DRAFT, confidence: "0.9" });
  return { c, send, tid };
}

function openReview(send: ReturnType<typeof setup>["send"], tid: string, artefact: unknown = DRAFT, to = "human:bob") {
  return send("review.request", { workspace: "wsp_b", from: "agent:bot", task_id: tid, to, artefact });
}

// ---- #72: the artefact under an open review cannot be swapped ----

test("re-requesting review with different content is refused", () => {
  const { send, tid, c } = setup();
  openReview(send, tid);
  const r = openReview(send, tid, SWAPPED);
  assert.equal(r.error?.code, -32014);
  assert.match(r.error!.message, /already open/);
  const task = (c as unknown as { workspaces: Map<string, { tasks: Map<string, { pending_artefact: unknown }> }> })
    .workspaces.get("wsp_b")!.tasks.get(tid)!;
  assert.deepEqual(task.pending_artefact, DRAFT, "the artefact under review is untouched");
});

test("re-requesting with the same content widens the reviewer set and keeps decisions", () => {
  const { send, tid, c } = setup();
  openReview(send, tid);
  const r = openReview(send, tid, DRAFT, "human:carol");
  assert.equal(r.error, undefined);
  assert.equal(r.result!.amended, true);
  assert.deepEqual(r.result!.requested_to, ["human:bob", "human:carol"]);

  // Carol, added by the amendment, is now eligible.
  const decided = send("decide.approve", { workspace: "wsp_b", from: "human:carol", task_id: tid });
  assert.equal(decided.error, undefined);
  assert.equal(decided.result!.state, "completed");
  void c;
});

test("the decision rule cannot be changed under an open review", () => {
  const { send, tid } = setup();
  openReview(send, tid);
  const r = send("review.request", { workspace: "wsp_b", from: "agent:bot", task_id: tid,
    to: "human:carol", artefact: DRAFT, rule: "quorum:2" });
  assert.equal(r.error?.code, -32014);
  assert.match(r.error!.message, /decision rule/);
});

// ---- #71: an approval binds the content it approved ----

test("a matching approved_artefact_digest is accepted", () => {
  const { send, tid } = setup();
  openReview(send, tid);
  const r = send("decide.approve", { workspace: "wsp_b", from: "human:bob", task_id: tid,
    approved_artefact_digest: contentHash(DRAFT) });
  assert.equal(r.error, undefined);
  assert.equal(r.result!.state, "completed");
});

test("a mismatched digest is refused and changes nothing", () => {
  const { send, tid, c } = setup();
  openReview(send, tid);
  const r = send("decide.approve", { workspace: "wsp_b", from: "human:bob", task_id: tid,
    approved_artefact_digest: contentHash(SWAPPED) });
  assert.equal(r.error?.code, -32074);
  const ws = (c as unknown as { workspaces: Map<string, {
    tasks: Map<string, { state: string; review: { decisions: unknown[] } }> }> }).workspaces.get("wsp_b")!;
  const task = ws.tasks.get(tid)!;
  assert.equal(task.state, "review_requested", "no state change on a refused decision");
  assert.equal(task.review.decisions.length, 0, "nothing recorded on a refused decision");
});

test("an absent digest behaves exactly as before", () => {
  const { send, tid } = setup();
  openReview(send, tid);
  const r = send("decide.approve", { workspace: "wsp_b", from: "human:bob", task_id: tid });
  assert.equal(r.error, undefined);
  assert.equal(r.result!.state, "completed");
});

test("a non-string digest is invalid params, not a mismatch", () => {
  const { send, tid } = setup();
  openReview(send, tid);
  const r = send("decide.approve", { workspace: "wsp_b", from: "human:bob", task_id: tid,
    approved_artefact_digest: 12345 });
  assert.equal(r.error?.code, -32602);
});

test("decide.override binds the base artefact the same way", () => {
  const { send, tid } = setup();
  openReview(send, tid);
  const bad = send("decide.override", { workspace: "wsp_b", from: "human:bob", task_id: tid,
    diff: [{ op: "replace", path: "/severity", value: "info" }], rationale: "false positive",
    approved_artefact_digest: contentHash(SWAPPED) });
  assert.equal(bad.error?.code, -32074);

  const ok = send("decide.override", { workspace: "wsp_b", from: "human:bob", task_id: tid,
    diff: [{ op: "replace", path: "/severity", value: "info" }], rationale: "false positive",
    approved_artefact_digest: contentHash(DRAFT) });
  assert.equal(ok.error, undefined);
});

test("the digest is the same construction the evidence chain uses", () => {
  // Not a new primitive: SHA-256 over JCS, prefixed, exactly as contentHash.
  assert.match(contentHash(DRAFT), /^sha256:[0-9a-f]{64}$/);
});
