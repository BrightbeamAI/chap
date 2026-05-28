/**
 * Smoke test for the playground backend.
 *
 * Boots a Coordinator with the routing policy, mocks the Ollama
 * agent (so the test doesn't need a local model), runs a ticket
 * end-to-end, and asserts:
 *   - The audit chain has the expected entries
 *   - A route_decision artefact was produced for review.depth
 *   - A critical-tier ticket triggers an escalate.auto decision
 *   - Sam is added as a reviewer when the rule fires
 *
 * Run with:  npm test
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator, makeDefaultPolicy } from "@chap/coordinator";
import { processTicket } from "../src/ollama-agent.js";
import { TICKETS, getTicket } from "../src/tickets.js";

const WS  = "wsp_test";
const BOT = "agent:triage-bot@local";
const MAYA = "human:maya@local";
const SAM  = "human:sam@local";

function setupCoordinator(): Coordinator {
  const coord = new Coordinator({ policy: makeDefaultPolicy(SAM) });

  coord.dispatch({ jsonrpc: "2.0", id: "1", method: "workspace.create",
    params: { workspace_id: WS, profiles: ["core/1.0", "review/1.0", "routing/1.0"] } });
  for (const [uri, type, role] of [
    [BOT, "agent", "drafter"], [MAYA, "human", "front-line"], [SAM, "human", "senior"],
  ] as const) {
    coord.dispatch({ jsonrpc: "2.0", id: `j-${uri}`, method: "participant.join",
      params: { workspace_id: WS, uri, type, role } });
  }
  return coord;
}

const mockDrafter = (confidence: number) => async (ticket: { subject: string }) => ({
  body:            `Draft for ${ticket.subject}`,
  tone:            "warm_professional",
  severity:        "low",
  self_confidence: confidence,
  raw_response:    "(mocked)",
  latency_ms:      42,
});

test("low-criticality ticket: review queued to Maya, no auto-escalation", async () => {
  const coord = setupCoordinator();
  const lowTicket = getTicket("INC-48219")!;
  assert.equal(lowTicket.routing_hints.criticality, "low");

  const taskId = await processTicket(coord, WS, lowTicket, MAYA, { drafter: mockDrafter(0.9) });

  const ws = coord.getWorkspace(WS)!;
  const task = ws.tasks.get(taskId)!;

  assert.equal(task.state, "review_requested");
  assert.ok(task.review, "review object should exist");
  assert.deepEqual(task.review!.requested_to, [MAYA],
    "low criticality + high confidence should NOT add Sam");

  // route_decision artefacts: one for review.depth, one for escalate.auto (no escalate)
  const decisions = Array.from(ws.route_decisions.values()).filter((a) => a.task_id === taskId);
  assert.equal(decisions.length, 2, "should have depth + escalate.auto decisions");

  const depth = decisions.find((d) => d.decision_type === "review.depth")!;
  assert.equal(depth.outcome, "spot_check", "confidence=0.9 should yield spot_check");
  assert.equal(depth.policy_id, "playground-default-v1");

  const esc = decisions.find((d) => d.decision_type === "escalate.auto")!;
  assert.deepEqual(esc.outcome, { escalate: false });
});

test("critical-tier ticket: auto-escalates to Sam", async () => {
  const coord = setupCoordinator();
  const critTicket = getTicket("INC-48224")!;
  assert.equal(critTicket.routing_hints.criticality, "critical");

  const taskId = await processTicket(coord, WS, critTicket, MAYA, { drafter: mockDrafter(0.8) });

  const ws = coord.getWorkspace(WS)!;
  const task = ws.tasks.get(taskId)!;

  assert.equal(task.state, "review_requested");
  assert.ok(task.review!.requested_to.includes(MAYA), "Maya should still be a reviewer");
  assert.ok(task.review!.requested_to.includes(SAM),  "Sam should be added on critical");

  const decisions = Array.from(ws.route_decisions.values()).filter((a) => a.task_id === taskId);
  const esc = decisions.find((d) => d.decision_type === "escalate.auto")!;
  const outcome = esc.outcome as { escalate: boolean; to: string };
  assert.equal(outcome.escalate, true);
  assert.equal(outcome.to, SAM);
});

test("high-criticality + low-confidence: also auto-escalates", async () => {
  const coord = setupCoordinator();
  const highTicket = getTicket("INC-48222")!;
  assert.equal(highTicket.routing_hints.criticality, "high");

  // Confidence 0.5 falls below the 0.6 threshold for high-criticality escalation.
  const taskId = await processTicket(coord, WS, highTicket, MAYA, { drafter: mockDrafter(0.5) });

  const ws = coord.getWorkspace(WS)!;
  const task = ws.tasks.get(taskId)!;
  assert.ok(task.review!.requested_to.includes(SAM), "Sam should be added");
});

test("decide.override applies the patch and writes an override artefact", async () => {
  const coord = setupCoordinator();
  const ticket = getTicket("INC-48219")!;
  const taskId = await processTicket(coord, WS, ticket, MAYA, { drafter: mockDrafter(0.9) });

  const overrideResp = coord.dispatch({
    jsonrpc: "2.0", id: "ov-1", method: "decide.override",
    params: {
      workspace_id: WS, task_id: taskId, from: MAYA,
      diff: [{ op: "replace", path: "/body", value: "Maya's revised version." }],
      rationale: "Tone too apologetic for a routine in-transit query.",
      tags: ["tone-softened"],
      // CHAP 0.2.1 — optional identity / intent fields.
      logical_id:       "lgl_01HZSPPRT0RESPND4821942KB5",
      intent_preserved: true,
    },
  });
  assert.ok(!overrideResp.error, `override should succeed, got: ${JSON.stringify(overrideResp.error)}`);

  const ws = coord.getWorkspace(WS)!;
  const task = ws.tasks.get(taskId)!;
  assert.equal(task.state, "completed");
  assert.equal((task.output as { body: string }).body, "Maya's revised version.");

  const overrides = Array.from(ws.overrides.values()).filter((o) => o.task_id === taskId);
  assert.equal(overrides.length, 1);
  assert.deepEqual(overrides[0].tags, ["tone-softened"]);
  assert.equal(overrides[0].reviewer, MAYA);

  // CHAP 0.2.1 — verify the new optional fields round-trip.
  assert.equal(overrides[0].logical_id,       "lgl_01HZSPPRT0RESPND4821942KB5");
  assert.equal(overrides[0].intent_preserved, true);
});

test("decide.override without identity fields still works (backward compat)", async () => {
  const coord = setupCoordinator();
  const ticket = getTicket("INC-48219")!;
  const taskId = await processTicket(coord, WS, ticket, MAYA, { drafter: mockDrafter(0.9) });

  const overrideResp = coord.dispatch({
    jsonrpc: "2.0", id: "ov-2", method: "decide.override",
    params: {
      workspace_id: WS, task_id: taskId, from: MAYA,
      diff: [{ op: "replace", path: "/body", value: "Legacy client output." }],
      rationale: "Pre-0.2.1 client; no identity fields.",
      tags: [],
    },
  });
  assert.ok(!overrideResp.error, `override should succeed, got: ${JSON.stringify(overrideResp.error)}`);

  const ws = coord.getWorkspace(WS)!;
  const overrides = Array.from(ws.overrides.values()).filter((o) => o.task_id === taskId);
  assert.equal(overrides.length, 1);
  assert.equal(overrides[0].logical_id,       undefined);
  assert.equal(overrides[0].intent_preserved, undefined);
});

test("audit chain has expected length and ordering", async () => {
  const coord = setupCoordinator();
  const ticket = getTicket("INC-48219")!;
  const taskId = await processTicket(coord, WS, ticket, MAYA, { drafter: mockDrafter(0.9) });

  coord.dispatch({
    jsonrpc: "2.0", id: "ap-1", method: "decide.approve",
    params: { workspace_id: WS, task_id: taskId, from: MAYA },
  });

  const ws = coord.getWorkspace(WS)!;
  // Expected envelopes appended to the chain:
  //   1 workspace.create
  //   3 participant.join (bot, Maya, Sam)
  //   1 task.create
  //   1 task.update (in_progress)
  //   1 task.complete (also fires routing decisions, but those are
  //                    written to route_decisions map, not the audit)
  //   1 decide.approve
  // Total: 8
  assert.equal(ws.audit.length, 8, `expected 8 audit entries, got ${ws.audit.length}`);

  // Methods in order
  const methods = ws.audit.map((e) => e.envelope.method);
  assert.deepEqual(methods, [
    "workspace.create",
    "participant.join",
    "participant.join",
    "participant.join",
    "task.create",
    "task.update",
    "task.complete",
    "decide.approve",
  ]);
});

test("ticket catalogue is internally consistent", () => {
  for (const t of TICKETS) {
    assert.ok(t.id.startsWith("INC-"));
    assert.ok(t.routing_hints.criticality);
    assert.ok(t.subject.length > 0);
    assert.ok(t.body.length > 0);
  }
});
