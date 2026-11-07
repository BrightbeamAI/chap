/**
 * The store a CHAP agent's refusals are kept in: the most recent ones only,
 * each without the request it refused, so memory stays bounded however
 * many calls are refused.
 *
 * Mirrors packages/coordinator-py/tests/test_a2a_task_store.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Role, TaskState, type Message, type Task } from "@a2a-js/sdk";
import { DefaultRequestHandler, ServerCallContext } from "@a2a-js/sdk/server";

import { Coordinator } from "@brightbeamai/chap-coordinator";

import { ChapTaskStore, makeChapAgentCard, makeChapAgentExecutor } from "../src/index.js";

const context = new ServerCallContext();

function message(id: string, role: Role, value: Record<string, unknown> = {}): Message {
  return {
    messageId: id, contextId: "c", taskId: "", role,
    parts: [{ content: { $case: "data", value }, metadata: undefined, filename: "", mediaType: "application/json" }],
    metadata: undefined, extensions: [], referenceTaskIds: [],
  };
}

function refusal(id: string): Task {
  return {
    id, contextId: "c", artifacts: [], metadata: undefined,
    status: { state: TaskState.TASK_STATE_REJECTED, message: message(`${id}-agent`, Role.ROLE_AGENT), timestamp: new Date().toISOString() },
    history: [message(`${id}-user`, Role.ROLE_USER, { pad: "x".repeat(1000) }), message(`${id}-agent`, Role.ROLE_AGENT)],
  };
}

test("it keeps the most recent tasks, the oldest going first", async () => {
  const store = new ChapTaskStore({ maxTasks: 2 });
  for (const id of ["t1", "t2", "t3"]) await store.save(refusal(id), context);
  assert.equal(await store.load("t1", context), undefined);
  assert.ok(await store.load("t2", context));
  assert.ok(await store.load("t3", context));

  // Saved again, a task becomes the newest.
  await store.save(refusal("t2"), context);
  await store.save(refusal("t4"), context);
  assert.equal(await store.load("t3", context), undefined);
  assert.deepEqual((await store.list({ tenant: "", contextId: "", status: TaskState.TASK_STATE_UNSPECIFIED, pageSize: 10,
                                        pageToken: "", historyLength: undefined, statusTimestampAfter: undefined,
                                        includeArtifacts: false }, context)).tasks.map((t) => t.id).sort(), ["t2", "t4"]);
});

test("a task is kept without the client's messages, and the agent's status message stays", async () => {
  const store = new ChapTaskStore();
  await store.save(refusal("t1"), context);
  const kept = (await store.load("t1", context))!;
  assert.deepEqual(kept.history.map((m) => m.role), [Role.ROLE_AGENT]);
  assert.equal(kept.status!.state, TaskState.TASK_STATE_REJECTED);
  assert.equal(kept.status!.message!.messageId, "t1-agent");
});

test("a caller sees only its own tasks", async () => {
  const store = new ChapTaskStore();
  await store.save(refusal("t1"), context);
  const other = new ServerCallContext({ user: { isAuthenticated: true, userName: "other" } });
  assert.equal(await store.load("t1", other), undefined);
  assert.ok(await store.load("t1", context));
});

test("maxTasks must be a whole number, 1 or more", () => {
  for (const maxTasks of [0, -1, 1.5]) assert.throws(() => new ChapTaskStore({ maxTasks }), RangeError);
});

test("behind the SDK's request handler, refused calls carrying a large body are not kept with it", async () => {
  const coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0"] });
  const store = new ChapTaskStore({ maxTasks: 5 });
  const handler = new DefaultRequestHandler(makeChapAgentCard({ baseUrl: "http://127.0.0.1/" }), store,
                                            makeChapAgentExecutor(coord));
  const ids: string[] = [];
  for (let i = 0; i < 8; i++) {
    const sent = message(`m${i}`, Role.ROLE_USER, { skill: "chap.workspace.describe",
                                                    params: { workspace: "missing", pad: "x".repeat(100_000) } });
    const reply = await handler.sendMessage({ tenant: "", message: { ...sent, contextId: "" }, configuration: undefined, metadata: undefined }, context);
    const task = reply as Task;
    assert.equal(task.status!.state, TaskState.TASK_STATE_REJECTED);
    ids.push(task.id);
  }
  const kept = await Promise.all(ids.map((id) => store.load(id, context)));
  assert.deepEqual(kept.map((t) => t !== undefined), [false, false, false, true, true, true, true, true]);
  for (const task of kept.filter((t): t is Task => t !== undefined)) {
    assert.ok(JSON.stringify(task).length < 2_000, "the request's 100 kB are not kept");
    assert.equal(task.history.some((m) => m.role === Role.ROLE_USER), false);
  }
  const fetched = await handler.getTask({ tenant: "", id: ids[7], historyLength: undefined }, context);
  assert.equal(fetched.status!.state, TaskState.TASK_STATE_REJECTED);
});
