/**
 * The checks an A2A server on the user's machine makes before any route.
 *
 * The TypeScript reference checked no Host, so a page that rebinds its own
 * name to 127.0.0.1 could drive the coordinator, and it answered an
 * over-limit body with Express's HTML error page, stack and file paths
 * included. chapA2aGuard makes the create-chap-app servers' checks: a
 * foreign Host is refused with 421, a foreign Origin with 403, a POST that
 * is not JSON with 415, and a body over the limit with 413, each as a short
 * JSON body.
 *
 * Mirrors packages/coordinator-py/tests/test_a2a_server_checks.py.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { request, type Server as HttpServer } from "node:http";

import express from "express";
import { AGENT_CARD_PATH } from "@a2a-js/sdk";
import { DefaultRequestHandler, InMemoryTaskStore } from "@a2a-js/sdk/server";
import { agentCardHandler, jsonRpcHandler, UserBuilder } from "@a2a-js/sdk/server/express";

import { Coordinator } from "@brightbeamai/chap-coordinator";

import { chapA2aGuard, makeChapAgentCard, makeChapAgentExecutor } from "../src/index.js";

let http: HttpServer;
let port: number;
let coord: Coordinator;

before(async () => {
  coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0", "review/1.0"] });
  const app = express();
  http = await new Promise<HttpServer>((resolve) => { const s = app.listen(0, "127.0.0.1", () => resolve(s)); });
  port = (http.address() as { port: number }).port;
  const handler = new DefaultRequestHandler(makeChapAgentCard({ baseUrl: `http://127.0.0.1:${port}/`, skillFilter: () => true }),
                                            new InMemoryTaskStore(), makeChapAgentExecutor(coord, { skillFilter: () => true }));
  app.use(chapA2aGuard({ allowedHosts: ["a2a.internal:9999"], maxBodyBytes: 300_000 }));
  app.use(`/${AGENT_CARD_PATH}`, agentCardHandler({ agentCardProvider: handler, legacyCompat: { enabled: true } }));
  app.use("/", jsonRpcHandler({ requestHandler: handler, userBuilder: UserBuilder.noAuthentication,
                                legacyCompat: { enabled: true } }));
});

after(() => new Promise<void>((r) => http.close(() => r())));

interface Answer { status: number; headers: Record<string, string | string[] | undefined>; text: string }

function send(path: string, { method = "POST", headers = {}, body }: {
  method?: string; headers?: Record<string, string | string[]>; body?: string | string[];
} = {}): Promise<Answer> {
  return new Promise((resolve, reject) => {
    const r = request({ host: "127.0.0.1", port, path, method, headers: { host: `127.0.0.1:${port}`, ...headers } }, (res) => {
      let text = "";
      res.on("data", (c) => { text += c; });
      res.on("end", () => resolve({ status: res.statusCode!, headers: res.headers, text }));
    });
    r.on("error", reject);
    for (const chunk of Array.isArray(body) ? body : body === undefined ? [] : [body]) r.write(chunk);
    r.end();
  });
}

const createWorkspace = (workspace: string) => JSON.stringify({
  jsonrpc: "2.0", id: 1, method: "message/send",
  params: { message: { kind: "message", messageId: `m-${workspace}`, role: "user",
                       parts: [{ kind: "data", data: { skill: "chap.workspace.create", params: { workspace } } }] } },
});
const JSON_TYPE = { "content-type": "application/json" };

test("a foreign Host is refused with 421 before any route, as DNS rebinding would bring", async () => {
  const card = await send(`/${AGENT_CARD_PATH}`, { method: "GET", headers: { host: `rebind.evil.example:${port}` } });
  assert.equal(card.status, 421);
  const call = await send("/", { headers: { ...JSON_TYPE, host: `rebind.evil.example:${port}`, origin: `http://rebind.evil.example:${port}` },
                                 body: createWorkspace("rebound") });
  assert.equal(call.status, 421);
  assert.deepEqual(JSON.parse(call.text), { error: "unknown host; add it to the allowed hosts to serve under that name" });
  assert.equal(coord.getWorkspace("rebound"), undefined);
});

test("a name given in allowedHosts is served", async () => {
  const card = await send(`/${AGENT_CARD_PATH}`, { method: "GET", headers: { host: "a2a.internal:9999" } });
  assert.equal(card.status, 200);
});

test("a foreign or null Origin is refused with 403; the server's own, or none, is served", async () => {
  for (const origin of ["https://evil.example", "null"]) {
    const r = await send("/", { headers: { ...JSON_TYPE, origin }, body: createWorkspace("cross") });
    assert.equal(r.status, 403, origin);
  }
  assert.equal(coord.getWorkspace("cross"), undefined);
  const twice = await send("/", { headers: { ...JSON_TYPE, origin: [`http://127.0.0.1:${port}`, "https://evil.example"] },
                                  body: createWorkspace("cross") });
  assert.equal(twice.status, 403, "two Origin headers name no one origin");
  assert.equal((await send("/", { headers: { ...JSON_TYPE, origin: `http://127.0.0.1:${port}` }, body: createWorkspace("own") })).status, 200);
  assert.equal((await send("/", { headers: JSON_TYPE, body: createWorkspace("none") })).status, 200);
  assert.ok(coord.getWorkspace("own") && coord.getWorkspace("none"));
});

test("a POST that is not JSON is refused with 415, as a page's simple request is", async () => {
  for (const type of ["text/plain;charset=UTF-8", "application/x-www-form-urlencoded"]) {
    const r = await send("/", { headers: { "content-type": type }, body: createWorkspace("plain") });
    assert.equal(r.status, 415, type);
  }
  assert.equal((await send("/", { body: createWorkspace("plain") })).status, 415, "no content type");
  assert.equal(coord.getWorkspace("plain"), undefined);
});

test("a body over the limit is refused with 413 as JSON, with no stack or path", async () => {
  const big = createWorkspace("big").replace('"big"', `"big", "pad": "${"x".repeat(400_000)}"`);
  const declared = await send("/", { headers: { ...JSON_TYPE, "content-length": String(Buffer.byteLength(big)) }, body: big });
  assert.equal(declared.status, 413);
  assert.match(String(declared.headers["content-type"]), /^application\/json/);
  assert.deepEqual(JSON.parse(declared.text), { error: "the request body is over 300000 bytes" });
  assert.equal(declared.headers.connection, "close");

  const streamed = await send("/", { headers: { ...JSON_TYPE, "transfer-encoding": "chunked" },
                                     body: [big.slice(0, 200_000), big.slice(200_000)] });
  assert.equal(streamed.status, 413);
  assert.ok(!/node_modules|at \w+ \(/.test(declared.text + streamed.text));
});

test("a body under the limit reaches the routes parsed, above the SDK's own 100 kB parser limit", async () => {
  const body = JSON.stringify({
    jsonrpc: "2.0", id: 2, method: "message/send",
    params: { message: { kind: "message", messageId: "m-large", role: "user",
                         parts: [{ kind: "data", data: { skill: "chap.workspace.create",
                                                         params: { workspace: "large", pad: "y".repeat(200_000) } } }] } },
  });
  const r = await send("/", { headers: JSON_TYPE, body });
  assert.equal(r.status, 200, r.text.slice(0, 200));
  assert.ok(coord.getWorkspace("large"));
});

test("a body that is not JSON is a parse error", async () => {
  const r = await send("/", { headers: JSON_TYPE, body: "{not json" });
  assert.equal(r.status, 400);
  assert.deepEqual(JSON.parse(r.text), { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
});
