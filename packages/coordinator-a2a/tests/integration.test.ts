/**
 * Integration tests for the @brightbeamai/coordinator-a2a adapter.
 *
 * The @a2a-js/sdk doesn't ship an in-memory transport, so we exercise
 * the dispatch path directly: build a ``ChapAgentExecutor``, hand it
 * a synthesized ``RequestContext`` carrying a Message, capture the
 * events it publishes to a recording ``ExecutionEventBus``, and
 * verify the response shape.
 *
 * Mirrors packages/coordinator-py/tests/test_a2a_integration.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";

import type {
  AgentExecutionEvent,
  ExecutionEventBus,
  RequestContext as A2ARequestContext,
} from "@a2a-js/sdk/server";
import type { DataPart, Message } from "@a2a-js/sdk";

import { Coordinator } from "@brightbeamai/coordinator";
import { TOOL_NAMES } from "@brightbeamai/coordinator-mcp/schemas";

import {
  ChapAgentExecutor,
  dispatchA2aMessage,
  makeChapAgentCard,
  makeChapAgentExecutor,
} from "../src/index.js";

class RecordingBus extends EventEmitter implements ExecutionEventBus {
  readonly events: AgentExecutionEvent[] = [];
  private finishedFlag = false;

  publish(event: AgentExecutionEvent): void {
    this.events.push(event);
  }

  finished(): void {
    this.finishedFlag = true;
  }

  get isFinished(): boolean {
    return this.finishedFlag;
  }
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

function buildMessage(
  skillId: string,
  params: Record<string, unknown>,
  opts: { metadata?: Record<string, unknown> } = {},
): Message {
  const dataPart: DataPart = {
    kind: "data",
    data: { skill: skillId, params },
  };
  return {
    kind: "message",
    messageId: "m1",
    role: "user",
    parts: [dataPart],
    ...(opts.metadata ? { metadata: opts.metadata } : {}),
  };
}

function buildRequestContext(message: Message): A2ARequestContext {
  // The RequestContext class is constructed by the SDK; we simulate it
  // with the same shape our executor reads from.
  return {
    userMessage: message,
    taskId: "tsk-test-1",
    contextId: "ctx-test-1",
  } as A2ARequestContext;
}

function extractDataBlob(msg: Message): Record<string, unknown> {
  for (const part of msg.parts) {
    if (part.kind === "data") {
      return (part as DataPart).data;
    }
  }
  throw new Error("response message had no data part");
}

async function run(
  executor: ChapAgentExecutor,
  message: Message,
): Promise<RecordingBus> {
  const bus = new RecordingBus();
  await executor.execute(buildRequestContext(message), bus);
  return bus;
}

// ============================================================
// AgentCard
// ============================================================

test("agent card lists every CHAP method", () => {
  const card = makeChapAgentCard({ baseUrl: "http://localhost:9000" });
  assert.equal(card.skills.length, TOOL_NAMES.length,
    "skills should match the CHAP method count");
  const ids = new Set(card.skills.map((s) => s.id));
  assert.ok(ids.has("chap.workspace.create"));
  assert.ok(ids.has("chap.task.create"));
  assert.ok(ids.has("chap.decide.override"));
  assert.ok(ids.has("chap.deliberate.open"));
  assert.equal(card.url, "http://localhost:9000");
  assert.equal(card.protocolVersion, "0.3.0");
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
  const msg = buildMessage("chap.workspace.create", { workspace: "wsp_a" });
  const resp = dispatchA2aMessage(coord, msg);
  assert.ok("result" in resp && !resp.error);
  assert.equal((resp.result as { workspace: string }).workspace, "wsp_a");
});

test("dispatch via skill in metadata takes precedence", () => {
  const coord = makeCoord();
  const msg: Message = {
    kind: "message",
    messageId: "m1",
    role: "user",
    metadata: { skill: "chap.workspace.create" },
    parts: [{ kind: "data", data: { workspace: "wsp_b" } } satisfies DataPart],
  };
  const resp = dispatchA2aMessage(coord, msg);
  assert.ok("result" in resp && !resp.error);
  assert.equal((resp.result as { workspace: string }).workspace, "wsp_b");
});

test("dispatch unknown skill returns -32601", () => {
  const coord = makeCoord();
  const msg = buildMessage("not.a.chap.skill", {});
  const resp = dispatchA2aMessage(coord, msg);
  assert.equal(resp.error?.code, -32601);
});

test("dispatch CHAP error returns intact", () => {
  const coord = makeCoord();
  const msg = buildMessage("chap.workspace.describe", { workspace: "missing" });
  const resp = dispatchA2aMessage(coord, msg);
  assert.equal(resp.error?.code, -32602);
});

// ============================================================
// ChapAgentExecutor (async path through the SDK interface)
// ============================================================

test("executor publishes success as a data message", async () => {
  const coord = makeCoord();
  const executor = makeChapAgentExecutor(coord);
  const msg = buildMessage("chap.workspace.create", { workspace: "wsp_x" });
  const bus = await run(executor, msg);

  assert.equal(bus.events.length, 1);
  assert.ok(bus.isFinished);
  const responseMsg = bus.events[0] as Message;
  assert.equal(responseMsg.kind, "message");
  assert.equal(responseMsg.role, "agent");
  const blob = extractDataBlob(responseMsg);
  assert.equal(blob.workspace, "wsp_x");
});

test("executor publishes error with is_error metadata", async () => {
  const coord = makeCoord();
  const executor = makeChapAgentExecutor(coord);
  const msg = buildMessage("chap.workspace.describe", { workspace: "nope" });
  const bus = await run(executor, msg);

  assert.equal(bus.events.length, 1);
  const responseMsg = bus.events[0] as Message;
  const part = responseMsg.parts[0] as DataPart;
  assert.equal(part.data.chap_error, -32602);
  assert.equal((part.metadata as { is_error?: boolean } | undefined)?.is_error, true);
});

test("executor rejects unknown skill cleanly", async () => {
  const coord = makeCoord();
  const executor = makeChapAgentExecutor(coord);
  const msg = buildMessage("not.a.chap.skill", {});
  const bus = await run(executor, msg);

  assert.equal(bus.events.length, 1);
  const responseMsg = bus.events[0] as Message;
  const part = responseMsg.parts[0] as DataPart;
  assert.equal(part.data.chap_error, -32601);
});

test("executor full workflow exercises every shipped profile via A2A", async () => {
  const coord = makeCoord();
  const executor = makeChapAgentExecutor(coord);

  const dispatch = async (skill: string, params: Record<string, unknown>) => {
    const bus = await run(executor, buildMessage(skill, params));
    assert.equal(bus.events.length, 1, `${skill}: expected 1 event`);
    const blob = extractDataBlob(bus.events[0] as Message);
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
  const coord = makeCoord();
  const executor = makeChapAgentExecutor(coord);
  const dispatch = async (skill: string, params: Record<string, unknown>) => {
    const bus = await run(executor, buildMessage(skill, params));
    return extractDataBlob(bus.events[0] as Message);
  };

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

test("cancelTask marks subsequent executions canceled", async () => {
  const coord = makeCoord();
  const executor = makeChapAgentExecutor(coord);
  const bus = new RecordingBus();
  await executor.cancelTask("tsk-test-1", bus);

  const msg = buildMessage("chap.workspace.create", { workspace: "wsp_cancel" });
  const bus2 = await run(executor, msg);

  // Should publish a status-update with state canceled.
  assert.ok(bus2.events.length >= 1);
  const firstEvent = bus2.events[0] as { kind: string; status?: { state?: string }};
  assert.equal(firstEvent.kind, "status-update");
  assert.equal(firstEvent.status?.state, "canceled");
});
