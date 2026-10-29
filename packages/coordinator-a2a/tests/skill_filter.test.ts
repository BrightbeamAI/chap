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

import type { AgentExecutionEvent, ExecutionEventBus, RequestContext } from "@a2a-js/sdk/server";
import type { DataPart, Message } from "@a2a-js/sdk";

import { Coordinator } from "@brightbeamai/chap-coordinator";

import { makeChapAgentExecutor } from "../src/index.js";

class RecordingBus extends EventEmitter implements ExecutionEventBus {
  readonly events: AgentExecutionEvent[] = [];
  publish(event: AgentExecutionEvent): void { this.events.push(event); }
  finished(): void {}
}

function message(skill: string, params: Record<string, unknown>): Message {
  return { kind: "message", messageId: "m1", role: "user",
           parts: [{ kind: "data", data: { skill, params } } as DataPart] };
}

async function run(executor: ReturnType<typeof makeChapAgentExecutor>, msg: Message) {
  const bus = new RecordingBus();
  await executor.execute({ userMessage: msg, taskId: "t1", contextId: "c1" } as RequestContext, bus);
  return (bus.events[0] as Message).parts[0] as DataPart;
}

test("a skill the filter excludes is refused and runs nothing", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const executor = makeChapAgentExecutor(coord, { skillFilter: (id) => id !== "chap.workspace.create" });
  const part = await run(executor, message("chap.workspace.create", { workspace: "w" }));
  assert.equal(part.data.chap_error, -32601);
  assert.match(String(part.data.message), /not offered by this server: chap\.workspace\.create/);
  assert.equal(coord.workspaces.get("w"), undefined);
});

test("a skill the filter keeps runs as before", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const executor = makeChapAgentExecutor(coord, { skillFilter: (id) => id === "chap.workspace.create" });
  const part = await run(executor, message("chap.workspace.create", { workspace: "w" }));
  assert.equal(part.data.workspace, "w");
});

test("without a filter every skill runs", async () => {
  const coord = new Coordinator({ deterministicIds: true });
  const part = await run(makeChapAgentExecutor(coord), message("chap.workspace.create", { workspace: "w" }));
  assert.equal(part.data.workspace, "w");
});
