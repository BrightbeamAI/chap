/**
 * The executor pins `from` to one participant, and a refusal is a failure
 * in A2A's terms.
 *
 * The executor had no participant option, so an A2A connection meant for one
 * agent could act in any member's name, a person's included. And a refusal
 * came back as a successful message carrying data.chap_error, so a client
 * reading A2A states saw every refused call succeed. A refusal is now a task
 * the agent rejected, and a fault in the server a task that failed, without
 * the exception's text.
 *
 * Mirrors packages/coordinator-py/tests/test_a2a_participant_and_refusals.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";

import {
  RequestContext,
  ServerCallContext,
  type AgentExecutionEvent,
  type ExecutionEventBus,
} from "@a2a-js/sdk/server";
import { Role, TaskState, type Message, type Task } from "@a2a-js/sdk";

import { Coordinator } from "@brightbeamai/chap-coordinator";

import { dispatchA2aMessage, makeChapAgentExecutor, type ChapAgentExecutorOptions } from "../src/index.js";

class RecordingBus extends EventEmitter implements ExecutionEventBus {
  readonly events: AgentExecutionEvent[] = [];
  publish(event: AgentExecutionEvent): void { this.events.push(event); }
  finished(): void {}
}

function message(skill: string, params: Record<string, unknown>): Message {
  return {
    messageId: "m1", contextId: "", taskId: "", role: Role.ROLE_USER,
    parts: [{ content: { $case: "data", value: { skill, params } }, metadata: undefined, filename: "", mediaType: "application/json" }],
    metadata: undefined, extensions: [], referenceTaskIds: [],
  };
}

function coordWithMembers(): Coordinator {
  const coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0", "review/1.0"] });
  for (const [method, params] of [
    ["workspace.create", { from: "human:alice" }],
    ["participant.join", { from: "human:alice", type: "human" }],
    ["participant.join", { from: "agent:orchestrator", type: "agent" }],
  ] as const) {
    coord.dispatch({ jsonrpc: "2.0", id: method, method, params: { workspace: "w", ...params } });
  }
  return coord;
}

async function run(coord: Pick<Coordinator, "dispatch">, msg: Message, options: ChapAgentExecutorOptions = {}) {
  const bus = new RecordingBus();
  await makeChapAgentExecutor(coord as Coordinator, options).execute(
    new RequestContext({ tenant: "", message: msg, configuration: undefined, metadata: undefined },
                       "task-1", "ctx-1", new ServerCallContext()), bus);
  assert.equal(bus.events.length, 1);
  return bus.events[0];
}

/** A refused call's task, and the CHAP error its status message carries. */
function refusal(event: AgentExecutionEvent): { task: Task; error: Record<string, any> } {
  assert.equal(event.kind, "task", "a refusal is a task");
  const task = event.data as Task;
  const part = task.status!.message!.parts[0];
  assert.equal(part.mediaType, "application/json");
  return { task, error: (part.content as { value: Record<string, any> }).value };
}

test("a refusal is a rejected task, and its status message carries the CHAP error", async () => {
  const event = await run(coordWithMembers(), message("chap.workspace.describe", { workspace: "missing" }));
  const { task, error } = refusal(event);
  assert.equal(task.status!.state, TaskState.TASK_STATE_REJECTED);
  assert.equal(task.id, "task-1");
  assert.equal(task.contextId, "ctx-1");
  assert.equal(task.status!.message!.role, Role.ROLE_AGENT);
  assert.equal(task.status!.message!.taskId, "task-1");
  assert.equal(error.chap_error, -32602);
  assert.equal(typeof error.message, "string");
  assert.equal(task.history[0].messageId, "m1", "the history holds the request");
});

test("an answer is still an agent message, with no task", async () => {
  const event = await run(coordWithMembers(), message("chap.workspace.describe", { workspace: "w" }));
  assert.equal(event.kind, "message");
  assert.equal((event.data as Message).taskId, "");
});

test("a fault in the server is a failed task, and its text stays in the server's log", async () => {
  const fault = new Error("EACCES: permission denied, open '/srv/chap/data/chap.db'");
  const logged: unknown[][] = [];
  const consoleError = console.error;
  console.error = (...args: unknown[]) => { logged.push(args); };
  let event: AgentExecutionEvent;
  try {
    event = await run({ dispatch() { throw fault; } }, message("chap.workspace.describe", { workspace: "w" }));
  } finally {
    console.error = consoleError;
  }
  const { task, error } = refusal(event);
  assert.equal(task.status!.state, TaskState.TASK_STATE_FAILED);
  assert.equal(error.chap_error, -32603);
  assert.equal(error.message, "The CHAP call failed inside the server; its log has the detail.");
  assert.ok(!JSON.stringify(task).includes("/srv/chap"));
  assert.ok(logged.some((args) => args.includes(fault)), "the error is logged");
});

test("a coordinator's internal error is a failed task, without the detail its data carried", async () => {
  // The coordinators answer a fault in a handler with -32603 and the
  // exception's text in data.detail.
  const internal = { code: -32603, message: "Internal error", data: { detail: "rule.startsWith is not a function at /srv/chap/coordinator.js:1801" } };
  const logged: unknown[][] = [];
  const consoleError = console.error;
  console.error = (...args: unknown[]) => { logged.push(args); };
  let event: AgentExecutionEvent;
  try {
    event = await run({ dispatch: (envelope) => ({ jsonrpc: "2.0", id: envelope.id, error: internal }) },
                      message("chap.review.request", { workspace: "w", rule: 5 }));
  } finally {
    console.error = consoleError;
  }
  const { task, error } = refusal(event);
  assert.equal(task.status!.state, TaskState.TASK_STATE_FAILED);
  assert.deepEqual(error, { chap_error: -32603, message: "The CHAP call failed inside the server; its log has the detail." });
  assert.ok(!JSON.stringify(task).includes("startsWith"));
  assert.ok(logged.some((args) => args.includes(internal)), "the detail is logged");
});

test("participant fills in from on every call", async () => {
  const coord = coordWithMembers();
  const event = await run(coord, message("chap.task.create", { workspace: "w", kind: "k", input: {}, assignee: "agent:orchestrator" }),
                          { participant: "agent:orchestrator" });
  assert.equal(event.kind, "message");
  const taskId = (((event.data as Message).parts[0].content as { value: { task_id: string } }).value).task_id;
  assert.equal(coord.getWorkspace("w")!.tasks.get(taskId)!.delegator, "agent:orchestrator");
});

test("with participant, a call naming a person is refused with -32011 and nothing runs", async () => {
  const coord = coordWithMembers();
  const before = coord.getWorkspace("w")!.audit.length;
  const event = await run(coord, message("chap.participant.join", { workspace: "w", from: "human:ceo@corp.example", type: "human" }),
                          { participant: "agent:orchestrator" });
  const { task, error } = refusal(event);
  assert.equal(task.status!.state, TaskState.TASK_STATE_REJECTED);
  assert.equal(error.chap_error, -32011);
  assert.equal(error.message, 'This server acts as agent:orchestrator; a call cannot name another from ("human:ceo@corp.example").');
  assert.equal(coord.getWorkspace("w")!.audit.length, before);
  assert.equal(coord.getWorkspace("w")!.members.get("human:ceo@corp.example"), undefined);
});

test("without participant each call names its own from, as before", () => {
  const coord = coordWithMembers();
  const reply = dispatchA2aMessage(coord, message("chap.task.create",
    { workspace: "w", from: "human:alice", kind: "k", input: {}, assignee: "agent:orchestrator" }));
  const taskId = (reply.result as { task_id: string }).task_id;
  assert.equal(coord.getWorkspace("w")!.tasks.get(taskId)!.delegator, "human:alice");
});
