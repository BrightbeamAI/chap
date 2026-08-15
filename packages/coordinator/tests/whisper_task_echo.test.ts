/**
 * whisper.answer echoes the whisper's task_id onto the recorded envelope, so a
 * task-filtered audit.read returns the answer alongside the ask. The value is
 * taken from the stored whisper, never from the caller, so an answer cannot be
 * filed against a different task.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.ts";

const PROFILES = ["core/1.0", "whisper/1.0", "audit-scitt/1.0"];

function setup() {
  const c = new Coordinator({ defaultProfiles: PROFILES });
  const s = (m: string, params: Record<string, unknown>) =>
    c.dispatch({ jsonrpc: "2.0", id: m, method: m, params } as never) as { result?: any; error?: any };
  s("workspace.create", { workspace: "w", profiles: PROFILES });
  s("participant.join", { workspace: "w", from: "agent:bot", type: "agent" });
  s("participant.join", { workspace: "w", from: "human:me", type: "human" });
  return s;
}
const task = (s: ReturnType<typeof setup>, kind = "x") =>
  s("task.create", { workspace: "w", from: "agent:bot", kind, input: {}, assignee: "agent:bot" }).result.task_id;
const ask = (s: ReturnType<typeof setup>, taskId: string) =>
  s("whisper.ask", { workspace: "w", from: "agent:bot", to: "human:me", task_id: taskId, question: "Proceed?",
    deadline_ms: 60000, default_if_lapsed: "yes", options: [{ id: "yes", label: "Yes" }] }).result.whisper_id;
const methods = (s: ReturnType<typeof setup>, taskId: string) =>
  s("audit.read", { workspace: "w", from: "human:me", filter: { task_id: taskId } })
    .result.entries.map((e: any) => e.envelope.method);

test("a task-filtered read returns the answer alongside the ask", () => {
  const s = setup();
  const tid = task(s);
  const wid = ask(s, tid);
  s("whisper.answer", { workspace: "w", from: "human:me", whisper_id: wid, answer_option: "yes" });
  assert.deepEqual(methods(s, tid), ["whisper.ask", "whisper.answer"]);
});

test("the answer result carries task_id", () => {
  const s = setup();
  const tid = task(s);
  const wid = ask(s, tid);
  const r = s("whisper.answer", { workspace: "w", from: "human:me", whisper_id: wid, answer_option: "yes" }).result;
  assert.equal(r.task_id, tid);
});

test("a caller-supplied task_id cannot mis-file the answer", () => {
  const s = setup();
  const real = task(s, "x");
  const other = task(s, "y");
  const wid = ask(s, real);
  s("whisper.answer", { workspace: "w", from: "human:me", whisper_id: wid, answer_option: "yes", task_id: other });
  assert.ok(methods(s, real).includes("whisper.answer"));
  assert.ok(!methods(s, other).includes("whisper.answer"));
});
