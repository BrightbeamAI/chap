/**
 * The executor refuses the skills its filter excludes.
 *
 * `skillFilter` was an option of the Agent Card alone, which only lists
 * skills: the executor ran any `chap.<method>` it was sent. The executor now
 * takes the same filter and refuses what it excludes with -32601.
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

import { dispatchA2aMessage, makeChapAgentExecutor } from "../src/index.js";

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

test("a skill the filter keeps runs as before", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const executor = makeChapAgentExecutor(coord, { skillFilter: (id) => id === "chap.workspace.create" });
  const data = await run(executor, message("chap.workspace.create", { workspace: "w" }));
  assert.equal(data.workspace, "w");
});

test("without a filter every skill runs", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const data = await run(makeChapAgentExecutor(coord), message("chap.workspace.create", { workspace: "w" }));
  assert.equal(data.workspace, "w");
});

test("dispatchA2aMessage refuses an excluded skill", () => {
  const coord = new Coordinator({ deterministicIds: true });
  const reply = dispatchA2aMessage(coord, message("chap.workspace.create", { workspace: "w" }), { skillFilter: () => false });
  assert.equal(reply.error?.code, -32601);
  assert.equal(coord.workspaces.get("w"), undefined);
});
