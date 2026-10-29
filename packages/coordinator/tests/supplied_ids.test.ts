/**
 * A supplied whisper, deliberation or handoff id names a new one.
 *
 * `whisper.ask`, `deliberate.open` and `handoff.propose` take an optional id,
 * and stored the new object under it whatever it held: a closed deliberation
 * could be opened again with its votes and outcome erased, an answered
 * whisper reset, and a pending handoff replaced. An id already in use, one
 * that is not a string, or a name JavaScript treats specially as an object
 * key, is now refused with -32602, and nothing changes. The TypeScript
 * coordinator dropped an object stored under __proto__ when it saved the
 * workspace, so it was gone after a restart.
 *
 * Mirrors packages/coordinator-py/tests/test_supplied_ids.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Coordinator } from "../src/coordinator.js";
import { E } from "../src/jsonrpc.js";

const PROFILES = ["core/1.0", "whisper/1.0", "deliberation/1.0", "handoff/1.0"];

function ready() {
  const c = new Coordinator({ defaultProfiles: PROFILES, deterministicIds: true });
  const send = (method: string, params: Record<string, unknown> = {}, actor = "human:a") =>
    c.dispatch({ jsonrpc: "2.0", id: method, method,
                 params: { workspace: "w", from: actor, ...params } }) as any;
  send("workspace.create", { profiles: PROFILES });
  for (const [uri, type] of [["human:a", "human"], ["human:c", "human"], ["agent:b", "agent"]])
    send("participant.join", { type }, uri);
  const task = send("task.create", { kind: "k", input: {}, assignee: "agent:b" }).result.task_id;
  return { c, send, task };
}

const ws = (c: any) => c.workspaces.get("w");

test("a closed deliberation cannot be opened again under its id", () => {
  const { c, send } = ready();
  assert.equal(send("deliberate.open", { deliberation_id: "d1", to: ["human:a", "human:c"],
                                         rule: "any_one_approves", question: "q" }).result.deliberation_id, "d1");
  send("deliberate.vote", { deliberation_id: "d1", vote: "yea" });
  send("deliberate.vote", { deliberation_id: "d1", vote: "yea" }, "human:c");
  send("deliberate.close", { deliberation_id: "d1" });
  const outcome = ws(c).deliberations.get("d1").outcome;
  assert.ok(outcome);

  const again = send("deliberate.open", { deliberation_id: "d1", to: ["human:a"], rule: "any_one_approves", question: "q2" });
  assert.equal(again.error?.code, E.PARAMS);
  assert.match(again.error.message, /deliberation_id already in use: d1/);
  assert.deepEqual(ws(c).deliberations.get("d1").outcome, outcome);
});

test("an answered whisper cannot be reset under its id", () => {
  const { c, send, task } = ready();
  const ask = { task_id: task, to: "human:a", question: "q", deadline_ms: 60_000, default_if_lapsed: "x" };
  assert.equal(send("whisper.ask", { ...ask, whisper_id: "w1" }, "agent:b").result.whisper_id, "w1");
  send("whisper.answer", { whisper_id: "w1", answer: "yes" });
  assert.equal(ws(c).whispers.get("w1").state, "answered");

  const again = send("whisper.ask", { ...ask, whisper_id: "w1" }, "agent:b");
  assert.equal(again.error?.code, E.PARAMS);
  assert.match(again.error.message, /whisper_id already in use: w1/);
  assert.equal(ws(c).whispers.get("w1").state, "answered");
});

test("a pending handoff cannot be replaced under its id", () => {
  const { c, send, task } = ready();
  assert.equal(send("handoff.propose", { handoff_id: "h1", to: "human:c", tasks: [{ task_id: task }] }, "agent:b")
    .result.handoff_id, "h1");
  const again = send("handoff.propose", { handoff_id: "h1", to: "human:a", tasks: [{ task_id: task }] }, "agent:b");
  assert.equal(again.error?.code, E.PARAMS);
  assert.match(again.error.message, /handoff_id already in use: h1/);
  assert.equal(ws(c).handoffs.get("h1").recipient, "human:c");
});

test("an id that is not a string is refused", () => {
  const { send, task } = ready();
  const r = send("whisper.ask", { task_id: task, to: "human:a", question: "q", deadline_ms: 1,
                                  default_if_lapsed: "x", whisper_id: 7 }, "agent:b");
  assert.equal(r.error?.code, E.PARAMS);
  assert.match(r.error.message, /whisper_id must be a string/);
});

test("a name JavaScript treats specially as an object key is refused as an id", () => {
  const { c, send, task } = ready();
  for (const id of ["__proto__", "constructor", "prototype"]) {
    const d = send("deliberate.open", { deliberation_id: id, to: ["human:a"], rule: "any_one_approves", question: "q" });
    assert.equal(d.error?.code, E.PARAMS, id);
    assert.equal(d.error.message, `deliberation_id cannot be ${JSON.stringify(id)}`);
    const w = send("whisper.ask", { task_id: task, to: "human:a", question: "q", deadline_ms: 60_000,
                                    default_if_lapsed: "x", whisper_id: id }, "agent:b");
    assert.equal(w.error?.code, E.PARAMS, id);
    assert.equal(w.error.message, `whisper_id cannot be ${JSON.stringify(id)}`);
    const h = send("handoff.propose", { handoff_id: id, to: "human:c", tasks: [{ task_id: task }] }, "agent:b");
    assert.equal(h.error?.code, E.PARAMS, id);
    assert.equal(h.error.message, `handoff_id cannot be ${JSON.stringify(id)}`);
  }
  assert.equal(ws(c).deliberations.size, 0);
  assert.equal(ws(c).whispers.size, 0);
  assert.equal(ws(c).handoffs.size, 0);
});

test("without an id, or with an empty one, an id is generated as before", () => {
  const { send } = ready();
  const a = send("deliberate.open", { to: ["human:a"], rule: "any_one_approves", question: "q" });
  const b = send("deliberate.open", { deliberation_id: "", to: ["human:a"], rule: "any_one_approves", question: "q" });
  assert.ok(a.result.deliberation_id);
  assert.ok(b.result.deliberation_id);
  assert.notEqual(a.result.deliberation_id, b.result.deliberation_id);
});
