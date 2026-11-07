/**
 * Integration tests for the @brightbeamai/chap-coordinator-a2a adapter.
 *
 * The executor is driven directly here: a ``RequestContext`` built as the
 * SDK builds one, carrying a Message, and a recording ``ExecutionEventBus``
 * that keeps what the executor publishes. round_trip.test.ts drives the same
 * executor through the SDK's server and client over HTTP.
 *
 * Mirrors packages/coordinator-py/tests/test_a2a_integration.py.
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
import { Role, type Message } from "@a2a-js/sdk";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import { TOOL_NAMES } from "@brightbeamai/chap-coordinator-mcp/schemas";

import {
  ChapAgentExecutor,
  dispatchA2aMessage,
  makeChapAgentCard,
  makeChapAgentExecutor,
} from "../src/index.js";

class RecordingBus extends EventEmitter implements ExecutionEventBus {
  readonly events: AgentExecutionEvent[] = [];
  isFinished = false;
  publish(event: AgentExecutionEvent): void { this.events.push(event); }
  finished(): void { this.isFinished = true; }
}

function makeCoord(): Coordinator {
  return new Coordinator({
    deterministicIds: true,
    deterministicClock: true,
    defaultProfiles: [
      "core/1.0", "review/1.0", "whisper/1.0",
      "deliberation/1.0", "handoff/1.0", "control/1.0",
      "routing/1.0", "audit-scitt/1.0",
    ],
  });
}

function buildMessage(skillId: string, params: Record<string, unknown>, metadata?: Record<string, unknown>): Message {
  return {
    messageId: "m1",
    contextId: "",
    taskId: "",
    role: Role.ROLE_USER,
    parts: [{ content: { $case: "data", value: { skill: skillId, params } }, metadata: undefined, filename: "", mediaType: "application/json" }],
    metadata,
    extensions: [],
    referenceTaskIds: [],
  };
}

function requestContext(message: Message): RequestContext {
  return new RequestContext({ tenant: "", message, configuration: undefined, metadata: undefined },
                            "tsk-test-1", "ctx-test-1", new ServerCallContext());
}

async function run(executor: ChapAgentExecutor, message: Message): Promise<RecordingBus> {
  const bus = new RecordingBus();
  await executor.execute(requestContext(message), bus);
  return bus;
}

/** The JSON of a published message's first part. */
function dataOf(event: AgentExecutionEvent): Record<string, any> {
  assert.equal(event.kind, "message");
  const part = (event.data as Message).parts[0];
  assert.equal(part.content?.$case, "data");
  assert.equal(part.mediaType, "application/json");
  return (part.content as { value: Record<string, any> }).value;
}

// ============================================================
// AgentCard
// ============================================================

test("agent card lists every CHAP method when asked to", () => {
  const card = makeChapAgentCard({ baseUrl: "http://localhost:9000", skillFilter: () => true });
  assert.equal(card.skills.length, TOOL_NAMES.length,
    "skills should match the CHAP method count");
  const ids = new Set(card.skills.map((s) => s.id));
  assert.ok(ids.has("chap.workspace.create"));
  assert.ok(ids.has("chap.task.create"));
  assert.ok(ids.has("chap.decide.override"));
  assert.ok(ids.has("chap.deliberate.open"));
});

test("agent card offers the endpoint at A2A 1.0, and at 0.3 for clients not yet moved", () => {
  const card = makeChapAgentCard({ baseUrl: "http://localhost:9000" });
  assert.deepEqual(card.supportedInterfaces, [
    { url: "http://localhost:9000", protocolBinding: "JSONRPC", tenant: "", protocolVersion: "1.0" },
    { url: "http://localhost:9000", protocolBinding: "JSONRPC", tenant: "", protocolVersion: "0.3" },
  ]);
});

test("agent card names its input and output modes by media type", () => {
  const card = makeChapAgentCard({ baseUrl: "http://localhost:9000" });
  assert.deepEqual(card.defaultInputModes, ["application/json"]);
  assert.deepEqual(card.defaultOutputModes, ["application/json"]);
  for (const skill of card.skills) {
    assert.deepEqual(skill.inputModes, ["application/json"], skill.id);
    assert.deepEqual(skill.outputModes, ["application/json"], skill.id);
  }
});

test("agent card skill descriptions are non-trivial", () => {
  const card = makeChapAgentCard({ baseUrl: "http://localhost:9000" });
  for (const skill of card.skills) {
    assert.ok(skill.description.length > 20,
      `trivial description on ${skill.id}: ${skill.description}`);
  }
});

test("agent card respects skillFilter", () => {
  const card = makeChapAgentCard({
    baseUrl: "http://localhost:9000",
    skillFilter: (name) => name.startsWith("chap.workspace"),
  });
  assert.ok(card.skills.length >= 1);
  for (const skill of card.skills) {
    assert.ok(skill.id.startsWith("chap.workspace"));
  }
});

test("agent card without baseUrl throws", () => {
  assert.throws(() => makeChapAgentCard({ baseUrl: "" }),
    /baseUrl is required/);
});

// ============================================================
// dispatchA2aMessage (direct functional path)
// ============================================================

test("dispatch via skill in data part", () => {
  const coord = makeCoord();
  const resp = dispatchA2aMessage(coord, buildMessage("chap.workspace.create", { workspace: "wsp_a" }));
  assert.ok("result" in resp && !resp.error);
  assert.equal((resp.result as { workspace: string }).workspace, "wsp_a");
});

test("dispatch via skill in metadata takes precedence", () => {
  const coord = makeCoord();
  const msg = buildMessage("chap.audit.read", {}, { skill: "chap.workspace.create" });
  msg.parts[0].content = { $case: "data", value: { workspace: "wsp_b" } };
  const resp = dispatchA2aMessage(coord, msg);
  assert.ok("result" in resp && !resp.error);
  assert.equal((resp.result as { workspace: string }).workspace, "wsp_b");
});

test("dispatch unknown skill returns -32601", () => {
  const resp = dispatchA2aMessage(makeCoord(), buildMessage("not.a.chap.skill", {}));
  assert.equal(resp.error?.code, -32601);
});

test("dispatch CHAP error returns intact", () => {
  const resp = dispatchA2aMessage(makeCoord(), buildMessage("chap.workspace.describe", { workspace: "missing" }));
  assert.equal(resp.error?.code, -32602);
});

// ============================================================
// ChapAgentExecutor (async path through the SDK interface)
// ============================================================

test("executor publishes success as an agent message carrying JSON", async () => {
  const bus = await run(makeChapAgentExecutor(makeCoord(), { skillFilter: () => true }), buildMessage("chap.workspace.create", { workspace: "wsp_x" }));
  assert.equal(bus.events.length, 1);
  assert.ok(bus.isFinished);
  const reply = bus.events[0].data as Message;
  assert.equal(reply.role, Role.ROLE_AGENT);
  assert.equal(reply.contextId, "ctx-test-1");
  assert.equal(dataOf(bus.events[0]).workspace, "wsp_x");
});

test("executor full workflow exercises every shipped profile via A2A", async () => {
  const coord = makeCoord();
  const executor = makeChapAgentExecutor(coord, { skillFilter: () => true });

  const dispatch = async (skill: string, params: Record<string, unknown>) => {
    const bus = await run(executor, buildMessage(skill, params));
    assert.equal(bus.events.length, 1, `${skill}: expected 1 event`);
    const blob = dataOf(bus.events[0]);
    if (typeof blob.chap_error === "number") {
      throw new Error(`${skill} errored: ${JSON.stringify(blob)}`);
    }
    return blob;
  };

  await dispatch("chap.workspace.create", { workspace: "wsp_flow" });

  for (const [from, type, role] of [
    ["human:alice", "human", "owner"],
    ["human:bob",   "human", "reviewer"],
    ["agent:bot",   "agent", "drafter"],
  ] as const) {
    await dispatch("chap.participant.join",
      { workspace: "wsp_flow", from, type, role });
  }

  const tBody = await dispatch("chap.task.create", {
    workspace: "wsp_flow", from: "human:alice", kind: "draft_response",
    assignee: "agent:bot", input: { subject: "test" },
  });
  const taskId = tBody.task_id as string;

  await dispatch("chap.task.update", {
    workspace: "wsp_flow", from: "agent:bot",
    task_id: taskId, state: "in_progress",
  });
  await dispatch("chap.task.complete", {
    workspace: "wsp_flow", from: "agent:bot", task_id: taskId,
    output: { body: "draft", severity: "warning" }, confidence: "0.85",
  });
  await dispatch("chap.review.request", {
    workspace: "wsp_flow", from: "agent:bot", task_id: taskId,
    to: "human:alice", rule: "any_one_approves",
    artefact: { body: "draft", severity: "warning" },
  });

  const overrideBody = await dispatch("chap.decide.override", {
    workspace: "wsp_flow", from: "human:alice", task_id: taskId,
    diff: [{ op: "replace", path: "/severity", value: "info" }],
    rationale: "false positive", tags: ["false-positive"],
  });
  const applied = overrideBody.applied as { severity: string };
  assert.equal(applied.severity, "info");

  const ws = coord.getWorkspace("wsp_flow")!;
  assert.equal(ws.tasks.get(taskId)!.state, "completed");
  assert.equal(ws.overrides.size, 1);
});

test("executor deliberation flow", async () => {
  const executor = makeChapAgentExecutor(makeCoord(), { skillFilter: () => true });
  const dispatch = async (skill: string, params: Record<string, unknown>) =>
    dataOf((await run(executor, buildMessage(skill, params))).events[0]);

  await dispatch("chap.workspace.create", { workspace: "wsp_d" });
  for (const u of ["human:a", "human:b", "human:c"]) {
    await dispatch("chap.participant.join",
      { workspace: "wsp_d", from: u, type: "human", role: "voter" });
  }
  const open = await dispatch("chap.deliberate.open", {
    workspace: "wsp_d", from: "human:a",
    to: ["human:a", "human:b", "human:c"],
    rule: "quorum:2", question: "ship it?",
  });
  const did = open.deliberation_id as string;

  for (const voter of ["human:a", "human:b"]) {
    await dispatch("chap.deliberate.vote",
      { workspace: "wsp_d", from: voter, deliberation_id: did, vote: "yea" });
  }
  const close = await dispatch("chap.deliberate.close",
    { workspace: "wsp_d", from: "human:a", deliberation_id: did });
  assert.equal(close.outcome, "approved");
  assert.deepEqual(close.tally, { yea: 2, nay: 0 });
});

test("cancelTask publishes nothing: a CHAP call has taken effect before it could be cancelled", async () => {
  const bus = new RecordingBus();
  await makeChapAgentExecutor(makeCoord(), { skillFilter: () => true }).cancelTask("tsk-test-1", bus);
  assert.equal(bus.events.length, 0);
  assert.ok(bus.isFinished);
});
