/**
 * The executor runs the skills its filter keeps, and refuses the others
 * with -32601; the Agent Card lists the same set. Unset, the filter keeps
 * the agent's skills, AGENT_MCP_TOOLS.
 *
 * Mirrors packages/coordinator-py/tests/test_a2a_skill_filter.py.
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
import { Role, type Message, type Task } from "@a2a-js/sdk";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import { TOOL_NAMES } from "@brightbeamai/chap-coordinator-mcp/schemas";
import { AGENT_MCP_TOOLS } from "@brightbeamai/chap-coordinator-mcp/tools";

import { dispatchA2aMessage, makeChapAgentCard, makeChapAgentExecutor } from "../src/index.js";

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

/** The JSON the executor answered with: an answer's own, or a refused task's status message. */
async function run(executor: ReturnType<typeof makeChapAgentExecutor>, msg: Message): Promise<Record<string, any>> {
  const bus = new RecordingBus();
  await executor.execute(new RequestContext({ tenant: "", message: msg, configuration: undefined, metadata: undefined },
                                            "t1", "c1", new ServerCallContext()), bus);
  const event = bus.events[0];
  const reply = event.kind === "task" ? (event.data as Task).status!.message! : event.data as Message;
  return (reply.parts[0].content as { value: Record<string, any> }).value;
}

test("a skill the filter excludes is refused and runs nothing", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const executor = makeChapAgentExecutor(coord, { skillFilter: (id) => id !== "chap.workspace.create" });
  const data = await run(executor, message("chap.workspace.create", { workspace: "w" }));
  assert.equal(data.chap_error, -32601);
  assert.match(String(data.message), /not offered by this server: chap\.workspace\.create/);
  assert.equal(coord.workspaces.get("w"), undefined);
});

test("a skill the filter keeps runs", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const executor = makeChapAgentExecutor(coord, { skillFilter: (id) => id === "chap.workspace.create" });
  const data = await run(executor, message("chap.workspace.create", { workspace: "w" }));
  assert.equal(data.workspace, "w");
});

test("without a filter the executor runs the agent's skills only, and the card lists those", async () => {
  const coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0", "control/1.0"] });
  coord.dispatch({ jsonrpc: "2.0", id: 1, method: "workspace.create", params: { workspace: "w", from: "agent:o" } });
  coord.dispatch({ jsonrpc: "2.0", id: 2, method: "participant.join", params: { workspace: "w", from: "agent:o", type: "agent" } });
  const executor = makeChapAgentExecutor(coord, { participant: "agent:o" });
  for (const skill of ["chap.control.pause", "chap.participant.join", "chap.workspace.set_profiles", "chap.review.request"]) {
    const data = await run(executor, message(skill, { workspace: "w", scope: "workspace", type: "agent", profiles: ["core/1.0"] }));
    assert.equal(data.chap_error, -32601, skill);
    assert.match(String(data.message), /not offered by this server/, skill);
  }
  assert.equal(coord.getWorkspace("w")!.state, "active");
  assert.equal((await run(executor, message("chap.workspace.describe", { workspace: "w" }))).id, "w");

  const card = makeChapAgentCard({ baseUrl: "http://127.0.0.1:9090/" });
  assert.deepEqual(new Set(card.skills.map((s) => s.id)), AGENT_MCP_TOOLS);
});

test("skillFilter: () => true offers every skill", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const data = await run(makeChapAgentExecutor(coord, { skillFilter: () => true }), message("chap.workspace.create", { workspace: "w" }));
  assert.equal(data.workspace, "w");
  assert.equal(makeChapAgentCard({ baseUrl: "http://127.0.0.1:9090/", skillFilter: () => true }).skills.length, TOOL_NAMES.length);
});

test("dispatchA2aMessage refuses an excluded skill", () => {
  const coord = new Coordinator({ deterministicIds: true });
  const reply = dispatchA2aMessage(coord, message("chap.workspace.create", { workspace: "w" }), { skillFilter: () => false });
  assert.equal(reply.error?.code, -32601);
  assert.equal(coord.workspaces.get("w"), undefined);
});
