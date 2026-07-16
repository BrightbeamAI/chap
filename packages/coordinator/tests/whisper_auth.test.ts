/**
 * Regression: whisper.answer may only be answered by an addressed askee (a
 * broadcast scope is satisfied by any member). Guards the 0.2.7 fix.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

function setup() {
  const c = new Coordinator({ defaultProfiles: ["core/1.0", "whisper/1.0"] });
  const s = (m: string, params: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m, params } as never) as { result?: any; error?: { code: number } };
  s("workspace.create", { workspace: "w", profiles: ["core/1.0", "whisper/1.0"] });
  s("participant.join", { workspace: "w", from: "agent:bot", type: "agent" });
  s("participant.join", { workspace: "w", from: "human:alice", type: "human" });
  s("participant.join", { workspace: "w", from: "human:mallory", type: "human" });
  const tid = s("task.create", { workspace: "w", from: "agent:bot", kind: "x", input: {}, assignee: "agent:bot" }).result.task_id;
  const wid = s("whisper.ask", { workspace: "w", from: "agent:bot", to: "human:alice", task_id: tid, question: "?", deadline_ms: 60000, default_if_lapsed: "no", options: [{ id: "yes" }] }).result.whisper_id;
  return { s, wid };
}

test("non-askee cannot answer a directed whisper", () => {
  const { s, wid } = setup();
  const r = s("whisper.answer", { workspace: "w", from: "human:mallory", whisper_id: wid, answer_option: "yes" });
  assert.ok(r.error && r.error.code === -32011);
});

test("addressed askee can answer", () => {
  const { s, wid } = setup();
  const r = s("whisper.answer", { workspace: "w", from: "human:alice", whisper_id: wid, answer_option: "yes" });
  assert.ok("result" in r);
});
