/**
 * The adapter behind the SDK's own server, reached by the SDK's own client.
 *
 * The package was on @a2a-js/sdk 0.3, so its card said protocolVersion
 * 0.3.0 and an A2A 1.0 client's SendMessage was answered with -32601. It is
 * now on the 1.x SDK: a 1.0 client finds the JSON-RPC interface on the card
 * and round-trips, and a 0.3 client, sending message/send with kind-tagged
 * parts and no A2A-Version header, is still answered, through the SDK's
 * compatibility layer. A refused call reaches either client as a task the
 * agent rejected.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import type { Server as HttpServer } from "node:http";

import express from "express";
import { AGENT_CARD_PATH, Role, TaskState, type Message, type Task } from "@a2a-js/sdk";
import { DefaultRequestHandler, InMemoryTaskStore } from "@a2a-js/sdk/server";
import { agentCardHandler, jsonRpcHandler, UserBuilder } from "@a2a-js/sdk/server/express";
import { ClientFactory, type Client } from "@a2a-js/sdk/client";

import { Coordinator } from "@brightbeamai/chap-coordinator";

import { makeChapAgentCard, makeChapAgentExecutor } from "../src/index.js";

let http: HttpServer;
let base: string;
let coord: Coordinator;

before(async () => {
  coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0", "review/1.0"] });
  const app = express();
  http = await new Promise<HttpServer>((resolve) => { const s = app.listen(0, "127.0.0.1", () => resolve(s)); });
  base = `http://127.0.0.1:${(http.address() as { port: number }).port}`;
  const handler = new DefaultRequestHandler(makeChapAgentCard({ baseUrl: `${base}/` }), new InMemoryTaskStore(),
                                            makeChapAgentExecutor(coord));
  app.use(`/${AGENT_CARD_PATH}`, agentCardHandler({ agentCardProvider: handler, legacyCompat: { enabled: true } }));
  app.use("/", jsonRpcHandler({ requestHandler: handler, userBuilder: UserBuilder.noAuthentication,
                                legacyCompat: { enabled: true } }));
});

after(() => new Promise<void>((r) => http.close(() => r())));

function message(skill: string, params: Record<string, unknown>): Message {
  return {
    messageId: `m-${skill}-${Math.random()}`, contextId: "", taskId: "", role: Role.ROLE_USER,
    parts: [{ content: { $case: "data", value: { skill, params } }, metadata: undefined, filename: "", mediaType: "application/json" }],
    metadata: undefined, extensions: [], referenceTaskIds: [],
  };
}

async function send(client: Client, skill: string, params: Record<string, unknown>): Promise<Record<string, any>> {
  const reply = await client.sendMessage({ tenant: "", message: message(skill, params), configuration: undefined, metadata: undefined });
  const part = (reply as Message).parts[0];
  assert.equal(part.content?.$case, "data", JSON.stringify(reply));
  return (part.content as { value: Record<string, any> }).value;
}

test("an A2A 1.0 client found by the card's URL drives a workspace", async () => {
  const client = await new ClientFactory().createFromUrl(base);
  assert.equal((await send(client, "chap.workspace.create", { workspace: "w1", from: "human:a" })).workspace, "w1");
  await send(client, "chap.participant.join", { workspace: "w1", from: "human:a", type: "human" });
  await send(client, "chap.participant.join", { workspace: "w1", from: "agent:b", type: "agent" });
  const made = await send(client, "chap.task.create", { workspace: "w1", from: "agent:b", kind: "k", input: {}, assignee: "agent:b", review_required: true });
  const done = await send(client, "chap.task.complete", { workspace: "w1", from: "agent:b", task_id: made.task_id, output: { body: "draft" } });
  assert.equal(done.state, "review_requested");
  const approved = await send(client, "chap.decide.approve", { workspace: "w1", from: "human:a", task_id: made.task_id });
  assert.equal(approved.state, "completed");
  assert.equal(coord.getWorkspace("w1")!.tasks.get(made.task_id)!.state, "completed");
});

test("the card a 1.0 client reads names the 1.0 JSON-RPC interface and media types", async () => {
  const r = await fetch(`${base}/${AGENT_CARD_PATH}`, { headers: { "A2A-Version": "1.0" } });
  const card = await r.json();
  assert.deepEqual(card.supportedInterfaces.map((i: any) => [i.protocolBinding, i.protocolVersion]),
                   [["JSONRPC", "1.0"], ["JSONRPC", "0.3"]]);
  assert.deepEqual(card.defaultInputModes, ["application/json"]);
});

test("a v0.3 client is still answered: the card and message/send in v0.3 shapes", async () => {
  const card = await (await fetch(`${base}/${AGENT_CARD_PATH}`)).json();
  assert.equal(card.url, `${base}/`);
  assert.equal(card.preferredTransport, "JSONRPC");
  assert.match(card.protocolVersion, /^0\.3/);

  const r = await fetch(`${base}/`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0", id: "1", method: "message/send",
      params: { message: { kind: "message", messageId: "m-v03", role: "user",
                           parts: [{ kind: "data", data: { skill: "chap.workspace.create", params: { workspace: "w03" } } }] } },
    }),
  });
  const body = await r.json();
  assert.equal(body.result.kind, "message", JSON.stringify(body));
  assert.equal(body.result.role, "agent");
  assert.equal(body.result.parts[0].kind, "data");
  assert.equal(body.result.parts[0].data.workspace, "w03");
});

test("a refused call reaches a 1.0 client as a rejected task", async () => {
  const client = await new ClientFactory().createFromUrl(base);
  const reply = await client.sendMessage({ tenant: "", configuration: undefined, metadata: undefined,
    message: message("chap.workspace.describe", { workspace: "missing" }) }) as Task;
  assert.equal(reply.status?.state, TaskState.TASK_STATE_REJECTED, JSON.stringify(reply));
  const data = (reply.status!.message!.parts[0].content as { value: Record<string, any> }).value;
  assert.equal(data.chap_error, -32602);
  // The SDK keeps the task: a client can read it back.
  const again = await client.getTask({ tenant: "", id: reply.id, historyLength: undefined });
  assert.equal(again.status?.state, TaskState.TASK_STATE_REJECTED);
});

test("a refused call reaches a v0.3 client as a task in state rejected", async () => {
  const r = await fetch(`${base}/`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0", id: "2", method: "message/send",
      params: { message: { kind: "message", messageId: "m-v03-refused", role: "user",
                           parts: [{ kind: "data", data: { skill: "chap.workspace.describe", params: { workspace: "missing" } } }] } },
    }),
  });
  const body = await r.json();
  assert.equal(body.result.kind, "task", JSON.stringify(body));
  assert.equal(body.result.status.state, "rejected");
  assert.equal(body.result.status.message.parts[0].kind, "data");
  assert.equal(body.result.status.message.parts[0].data.chap_error, -32602);
});
