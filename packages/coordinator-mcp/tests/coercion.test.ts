/**
 * Regression tests for stringified-JSON argument coercion.
 *
 * Reproduces the exact failure observed in a real Claude Desktop
 * integration: the MCP client serialised structured tool arguments
 * (`output`, `to`, `artefact`) as JSON-encoded strings rather than
 * native objects/arrays. That left the artefact under review stored as
 * a string, so a `decide.override` with an object-path JSON Patch
 * (`/draft`) crashed the coordinator with an internal error.
 *
 * After coercion at the adapter boundary, the whole chain is correctly
 * typed and the object-path override applies on the first try.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "@chap/coordinator";
import { dispatchToolCall, coerceToolArgs } from "../src/index.js";

function freshCoord(): Coordinator {
  return new Coordinator({
    deterministicIds: true,
    deterministicClock: true,
    defaultProfiles: ["core/1.0", "review/1.0", "routing/1.0"],
  });
}

describe("coerceToolArgs (unit)", () => {
  test("parses a stringified object for an opaque artefact field", () => {
    const out = coerceToolArgs("chap.task.complete", {
      workspace: "wsp_demo",
      from: "agent:bot@local",
      task_id: "tsk_1",
      output: '{"draft": "hello"}',
    });
    assert.deepEqual(out.output, { draft: "hello" });
  });

  test("parses a stringified array for review.request `to`", () => {
    const out = coerceToolArgs("chap.review.request", {
      workspace: "wsp_demo",
      from: "agent:bot@local",
      task_id: "tsk_1",
      to: '["human:me@local"]',
      artefact: '{"draft": "hi"}',
    });
    assert.deepEqual(out.to, ["human:me@local"]);
    assert.deepEqual(out.artefact, { draft: "hi" });
  });

  test("leaves a bare URI string untouched for a string|array field", () => {
    const out = coerceToolArgs("chap.review.request", {
      to: "human:me@local",
    });
    assert.equal(out.to, "human:me@local");
  });

  test("leaves an ordinary string field untouched", () => {
    const out = coerceToolArgs("chap.decide.override", {
      rationale: "warmer phrasing",
    });
    assert.equal(out.rationale, "warmer phrasing");
  });

  test("leaves a non-JSON string that happens to start with a brace", () => {
    // Not valid JSON: must be passed through, not dropped.
    const out = coerceToolArgs("chap.task.complete", {
      output: "{not valid json",
    });
    assert.equal(out.output, "{not valid json");
  });

  test("parses a stringified diff array for decide.override", () => {
    const out = coerceToolArgs("chap.decide.override", {
      diff: '[{"op":"replace","path":"/draft","value":"x"}]',
    });
    assert.deepEqual(out.diff, [{ op: "replace", path: "/draft", value: "x" }]);
  });

  test("does not mutate the input object", () => {
    const input = { output: '{"a":1}' };
    const out = coerceToolArgs("chap.task.complete", input);
    assert.equal(input.output, '{"a":1}'); // unchanged
    assert.deepEqual(out.output, { a: 1 });
  });
});

describe("stringified-JSON end-to-end: the Claude Desktop replay", () => {
  test("object-path override succeeds when args arrive stringified", () => {
    const coord = freshCoord();

    // Exactly the shapes Claude Desktop sent in the reported trail.
    dispatchToolCall(coord, "chap.workspace.create", {
      workspace: "wsp_demo",
      profiles: ["core/1.0", "review/1.0", "routing/1.0"],
    });
    dispatchToolCall(coord, "chap.participant.join", {
      workspace: "wsp_demo", from: "human:me@local", type: "human",
    });
    dispatchToolCall(coord, "chap.participant.join", {
      workspace: "wsp_demo", from: "agent:bot@local", type: "agent",
    });
    const created = dispatchToolCall(coord, "chap.task.create", {
      workspace: "wsp_demo",
      from: "human:me@local",
      kind: "draft_response",
      assignee: "agent:bot@local",
      input: '{"channel":"email"}',          // stringified
    });
    const taskId = (created.result as { task_id: string }).task_id;

    dispatchToolCall(coord, "chap.task.update", {
      workspace: "wsp_demo", from: "agent:bot@local",
      task_id: taskId, state: "in_progress",
    });
    dispatchToolCall(coord, "chap.task.complete", {
      workspace: "wsp_demo", from: "agent:bot@local", task_id: taskId,
      output: '{"draft": "Your order is in transit; updates within 24 hours"}', // stringified
      confidence: 0.9,
    });
    dispatchToolCall(coord, "chap.review.request", {
      workspace: "wsp_demo", from: "agent:bot@local", task_id: taskId,
      to: '["human:me@local"]',                                                 // stringified array
      artefact: '{"draft": "Your order is in transit; updates within 24 hours"}', // stringified
      rule: "any_one_approves",
    });

    // The override that crashed before: object-path patch on /draft.
    const overrideResp = dispatchToolCall(coord, "chap.decide.override", {
      workspace: "wsp_demo", from: "human:me@local", task_id: taskId,
      diff: [{ op: "replace", path: "/draft", value: "Your order is in transit; updates by tomorrow" }],
      rationale: "warmer phrasing",
      tags: ["tone-softened"],
      intent_preserved: true,
    });

    // No internal error, and the patched value is an object with the edit applied.
    assert.equal(overrideResp.error, undefined,
      `expected no error, got ${JSON.stringify(overrideResp.error)}`);
    const result = overrideResp.result as { state: string; applied: unknown };
    assert.equal(result.state, "completed");
    assert.deepEqual(result.applied, {
      draft: "Your order is in transit; updates by tomorrow",
    });
  });

  test("artefact is stored as a real object, not a string, in the audit log", () => {
    const coord = freshCoord();
    dispatchToolCall(coord, "chap.workspace.create", {
      workspace: "wsp_t", profiles: ["core/1.0", "review/1.0"],
    });
    dispatchToolCall(coord, "chap.participant.join", {
      workspace: "wsp_t", from: "human:me@local", type: "human",
    });
    dispatchToolCall(coord, "chap.participant.join", {
      workspace: "wsp_t", from: "agent:bot@local", type: "agent",
    });
    const created = dispatchToolCall(coord, "chap.task.create", {
      workspace: "wsp_t", from: "human:me@local", kind: "draft",
      assignee: "agent:bot@local", input: { x: 1 },
    });
    const taskId = (created.result as { task_id: string }).task_id;
    dispatchToolCall(coord, "chap.task.complete", {
      workspace: "wsp_t", from: "agent:bot@local", task_id: taskId,
      output: '{"draft": "hi"}',
    });

    const audit = dispatchToolCall(coord, "chap.audit.read", {
      workspace: "wsp_t", filter: { method: "task.complete" },
    });
    const entries = (audit.result as { entries: Array<{ envelope: { params: { output: unknown } } }> }).entries;
    assert.equal(entries.length, 1);
    assert.deepEqual(entries[0].envelope.params.output, { draft: "hi" });
  });
});
