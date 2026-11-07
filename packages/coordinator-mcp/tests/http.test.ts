/**
 * MCP over HTTP, in both protocol eras, on one endpoint.
 *
 * chapMcpHttpHandler answers a 2026-07-28 request as that revision's single
 * exchange, and hands the handshake era to the SDK's transport, with the
 * participant and tool set holding on both paths.
 *
 * The 2026-07-28 answers pinned here are the Python `mcp` SDK's to the same
 * requests, in status, error code and message, with these exceptions:
 *
 * - a POST that is not JSON is refused with 415, as the TypeScript SDK's
 *   transport refuses it on the handshake path, where the Python SDK
 *   answers 400;
 * - an error whose request id cannot be read carries no id, as the
 *   2026-07-28 schema has it, where the Python SDK sends `"id": null`;
 * - 413 and 415 carry -32600, since the revision asks new implementations
 *   not to use -32000 to -32019.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer, request, type Server as HttpServer } from "node:http";
import { connect } from "node:net";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

import {
  AGENT_MCP_TOOLS,
  META_CLIENT_CAPABILITIES,
  META_PROTOCOL_VERSION,
  chapMcpHttpHandler,
  type ChapDispatcher,
  type ChapMcpHttpOptions,
} from "../src/index.js";
import { mcpHttpHandler } from "../src/http.js";

const MODERN = "2026-07-28";
const HANDSHAKE = "2025-11-25";
const META = { [META_PROTOCOL_VERSION]: MODERN, [META_CLIENT_CAPABILITIES]: {} };

let coord: Coordinator;
let http: HttpServer;
let base: string;

/** Serve a handler on a port of its own; /parsed reads and parses the body first, as the templates' server does. */
async function serve(dispatcher: ChapDispatcher, options: ChapMcpHttpOptions): Promise<{ server: HttpServer; base: string }> {
  const mcp = chapMcpHttpHandler(dispatcher, options);
  const server = createServer(async (req, res) => {
    if (req.url === "/parsed" && req.method === "POST") {
      let text = "";
      for await (const chunk of req) text += chunk;
      return mcp(req, res, JSON.parse(text));
    }
    return mcp(req, res);
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  return { server, base: `http://127.0.0.1:${(server.address() as { port: number }).port}` };
}

const stop = (server: HttpServer) => new Promise<void>((r) => { server.closeAllConnections(); server.close(() => r()); });

before(async () => {
  coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0", "review/1.0"] });
  for (const [method, params] of [
    ["workspace.create", { from: "human:a" }],
    ["participant.join", { from: "human:a", type: "human" }],
    ["participant.join", { from: "agent:b", type: "agent" }],
  ] as const) {
    coord.dispatch({ jsonrpc: "2.0", id: method, method, params: { workspace: "w", ...params } });
  }
  ({ server: http, base } = await serve(coord, {
    participant: "agent:b",
    toolFilter: (name) => AGENT_MCP_TOOLS.has(name),
    maxBodyBytes: 4096,
  }));
});

after(() => stop(http));

interface Sent { status: number; headers: Record<string, string | string[] | undefined>; body: any }

/**
 * POST (or another method) to the endpoint, with the 2026-07-28 headers
 * unless told otherwise. A request with no answer within `wait` ms fails the
 * test, so a handler that never answers fails here and does not hang the run.
 */
function send(
  body: unknown,
  { method = "POST", path = "/mcp", headers = {} as Record<string, string | string[] | null>, raw, to = base, wait = 5000 }: {
    method?: string; path?: string; headers?: Record<string, string | string[] | null>; raw?: string; to?: string; wait?: number;
  } = {},
): Promise<Sent> {
  const rpcMethod = (body as { method?: string } | null)?.method;
  const all: Record<string, string | string[] | null> = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    "mcp-protocol-version": MODERN,
    ...(rpcMethod ? { "mcp-method": rpcMethod } : {}),
    ...headers,
  };
  return new Promise((resolve, reject) => {
    const r = request(to + path, { method }, (res) => {
      let text = "";
      res.on("data", (c) => { text += c; });
      res.on("end", () => { clearTimeout(timer); resolve({ status: res.statusCode!, headers: res.headers, body: text ? parse(text) : null }); });
    });
    const timer = setTimeout(() => { r.destroy(); reject(new Error(`no answer within ${wait} ms`)); }, wait);
    r.on("error", (err) => { clearTimeout(timer); reject(err); });
    for (const [name, value] of Object.entries(all)) if (value !== null) r.setHeader(name, value);
    r.end(raw ?? (body === undefined ? undefined : JSON.stringify(body)));
  });
}

/** A JSON body, or the JSON-RPC message an SSE stream carries. */
function parse(text: string): any {
  const event = /^data: (.*)$/m.exec(text);
  return JSON.parse(event ? event[1] : text);
}

const call = (id: number, name: string, args: Record<string, unknown>) =>
  ({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args, _meta: META } });

const list = (id: number) => ({ jsonrpc: "2.0", id, method: "tools/list", params: { _meta: META } });

// ------------------------------------------------------------------
// The 2026-07-28 single exchange
// ------------------------------------------------------------------

test("a 2026-07-28 tools/list is answered with one JSON object, and the agent's tools", async () => {
  const r = await send(list(1));
  assert.equal(r.status, 200);
  assert.match(String(r.headers["content-type"]), /^application\/json/);
  assert.equal(r.headers["mcp-session-id"], undefined);
  assert.equal(r.body.result.resultType, "complete");
  assert.deepEqual(r.body.result.tools.map((t: { name: string }) => t.name).sort(), [...AGENT_MCP_TOOLS].sort());
});

test("a 2026-07-28 tools/call runs as the participant, and another from is refused", async () => {
  const made = await send(call(2, "chap.task.create", { workspace: "w", kind: "k", input: {}, assignee: "agent:b" }),
    { headers: { "mcp-name": "chap.task.create" } });
  assert.equal(made.status, 200);
  assert.ok(!made.body.result.isError, made.body.result.content[0].text);
  const taskId = JSON.parse(made.body.result.content[0].text).task_id;
  assert.equal(coord.getWorkspace("w")!.tasks.get(taskId)!.delegator, "agent:b");

  const asHuman = await send(call(3, "chap.task.update", { workspace: "w", from: "human:a", task_id: taskId, state: "declined" }),
    { headers: { "mcp-name": "chap.task.update" } });
  assert.equal(asHuman.status, 200);
  assert.equal(asHuman.body.result.isError, true);
  assert.match(asHuman.body.result.content[0].text, /acts as agent:b/);

  const hidden = await send(call(4, "chap.participant.join", { workspace: "w", type: "agent" }),
    { headers: { "mcp-name": "chap.participant.join" } });
  assert.match(hidden.body.result.content[0].text, /not offered by this server/);
});

test("server/discover answers on the 2026-07-28 path", async () => {
  const r = await send({ jsonrpc: "2.0", id: 5, method: "server/discover", params: { _meta: META } });
  assert.equal(r.status, 200);
  assert.ok(r.body.result.supportedVersions.includes(MODERN));
});

test("a body the server has already parsed is used as it stands", async () => {
  const r = await send(list(6), { path: "/parsed" });
  assert.equal(r.status, 200);
  assert.equal(r.body.result.tools.length, AGENT_MCP_TOOLS.size);
});

test("the headers must match the body", async () => {
  const wrongMethod = await send(list(7), { headers: { "mcp-method": "tools/call" } });
  assert.equal(wrongMethod.status, 400);
  assert.equal(wrongMethod.body.error.code, -32020);
  assert.equal(wrongMethod.body.id, 7);

  const noName = await send(call(8, "chap.workspace.describe", { workspace: "w" }));
  assert.equal(noName.status, 400);
  assert.equal(noName.body.error.code, -32020);
  assert.match(noName.body.error.message, /mcp-name header does not match/);

  const encoded = `=?base64?${Buffer.from("chap.workspace.describe").toString("base64")}?=`;
  const named = await send(call(9, "chap.workspace.describe", { workspace: "w" }), { headers: { "mcp-name": encoded } });
  assert.equal(named.status, 200);

  const twice = await send(list(7), { headers: { "mcp-method": ["tools/list", "tools/list"] } });
  assert.equal(twice.status, 400);
  assert.match(twice.body.error.message, /mcp-method header appears more than once/);

  const otherVersion = await send({ ...list(7), params: { _meta: { ...META, [META_PROTOCOL_VERSION]: "2099-01-01" } } });
  assert.equal(otherVersion.status, 400);
  assert.equal(otherVersion.body.error.code, -32020);
});

test("an unsupported version is refused with -32022 and the versions to retry with", async () => {
  const r = await send({ jsonrpc: "2.0", id: 10, method: "tools/list",
                         params: { _meta: { ...META, [META_PROTOCOL_VERSION]: "2099-01-01" } } },
                       { headers: { "mcp-protocol-version": "2099-01-01" } });
  assert.equal(r.status, 400);
  assert.equal(r.body.error.code, -32022);
  assert.deepEqual(r.body.error.data, { supported: [MODERN], requested: "2099-01-01" });
});

test("an envelope without its required keys is invalid params", async () => {
  const r = await send({ jsonrpc: "2.0", id: 11, method: "tools/list", params: { _meta: { [META_PROTOCOL_VERSION]: MODERN } } });
  assert.equal(r.status, 400);
  assert.equal(r.body.error.code, -32602);
  assert.match(r.body.error.message, /missing the required envelope key\(s\): io\.modelcontextprotocol\/clientCapabilities/);
});

test("a method the 2026-07-28 revision does not have here is not found, initialize included", async () => {
  for (const method of ["initialize", "ping", "resources/list", "resources/read", "prompts/get"]) {
    const r = await send({ jsonrpc: "2.0", id: 12, method, params: { _meta: META } });
    assert.equal(r.status, 404, method);
    assert.deepEqual(r.body.error, { code: -32601, message: "Method not found", data: method });
  }
});

test("a notification is accepted with 202 and no body", async () => {
  const r = await send({ jsonrpc: "2.0", method: "notifications/cancelled", params: {} });
  assert.equal(r.status, 202);
  assert.equal(r.body, null);
  for (const malformed of [
    { jsonrpc: "2.0", method: "notifications/cancelled", params: 5 },
    { jsonrpc: "2.0", method: "notifications/cancelled", params: {}, extra: true },
  ]) {
    const refused = await send(malformed);
    assert.equal(refused.status, 400, JSON.stringify(malformed));
    assert.equal(refused.body.error.code, -32600);
    assert.ok(!("id" in refused.body));
  }
});

test("a tool call without a tool name, or with arguments that are not an object, is invalid params", async () => {
  for (const params of [{ arguments: {} }, { name: "chap.workspace.describe", arguments: [1] }]) {
    const r = await send({ jsonrpc: "2.0", id: 14, method: "tools/call", params: { ...params, _meta: META } },
                         { headers: { "mcp-name": "chap.workspace.describe" } });
    assert.equal(r.status, 400, JSON.stringify(params));
    assert.deepEqual(r.body.error, { code: -32602, message: "Invalid request parameters" });
  }
});

test("a request the SDK's schema refuses is answered 400 -32600 at once, and never left waiting", async () => {
  for (const body of [
    { ...list(20), extra: true },
    { ...list(21), params: { _meta: { ...META, progressToken: {} } } },
    { ...list(22), params: { _meta: { ...META, progressToken: 1.5 } } },
    { ...list(23), params: { _meta: { ...META, "io.modelcontextprotocol/related-task": { taskId: 7 } } } },
  ]) {
    const r = await send(body, { wait: 2000 });
    assert.equal(r.status, 400, JSON.stringify(body));
    assert.deepEqual(r.body.error, { code: -32600, message: "Body must be a single JSON-RPC request or notification object" });
    assert.equal(r.body.id, body.id);
  }
});

test("what is not one request or notification is refused, without an id where none can be read", async () => {
  const batch = await send([list(1)], { headers: { "mcp-method": "tools/list" } });
  assert.equal(batch.status, 400);
  assert.equal(batch.body.error.code, -32600);
  assert.ok(!("id" in batch.body));

  const garbled = await send(null, { raw: "{not json", headers: { "mcp-method": "tools/list" } });
  assert.equal(garbled.status, 400);
  assert.deepEqual(garbled.body, { jsonrpc: "2.0", error: { code: -32700, message: "Parse error" } });
});

test("the binding's HTTP rules: POST only, JSON accepted and sent, and a size limit", async () => {
  const get = await send(undefined, { method: "GET" });
  assert.equal(get.status, 405);
  assert.equal(get.headers.allow, "POST");

  assert.equal((await send(list(13), { headers: { accept: "text/html" } })).status, 406);
  const plain = await send(list(13), { headers: { "content-type": "text/plain" } });
  assert.equal(plain.status, 415);
  assert.equal(plain.body.error.code, -32600);

  const big = await send({ ...list(13), params: { _meta: META, pad: "x".repeat(8192) } });
  assert.equal(big.status, 413);
  assert.equal(big.body.error.code, -32600);
  assert.match(big.body.error.message, /must not exceed 4096 bytes/);
});

test("a body over the limit sent in chunks, with no length declared, is refused with 413", async () => {
  const status = await new Promise<number>((resolve, reject) => {
    const r = request(`${base}/mcp`, { method: "POST", headers: {
      "content-type": "application/json", accept: "application/json", "mcp-protocol-version": MODERN, "mcp-method": "tools/list",
    } }, (res) => { res.resume(); resolve(res.statusCode!); });
    r.on("error", reject);
    r.write(JSON.stringify(list(16)).slice(0, -1));
    for (let i = 0; i < 4; i++) r.write(`,"pad${i}":"${"x".repeat(2048)}"`);
    r.end("}");
  });
  assert.equal(status, 413);
});

/**
 * A complete workspace.create, sent on a socket of its own. Cut short, the
 * Content-Length declares 500 bytes more than are sent, and the connection
 * closes before they arrive.
 */
async function sendCreate(to: string, version: string, workspace: string, cutShort: boolean): Promise<void> {
  const body = Buffer.from(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: {
    name: "chap.workspace.create", arguments: { workspace },
    ...(version === MODERN ? { _meta: META } : {}),
  } }));
  const port = new URL(to).port;
  const socket = connect(Number(port), "127.0.0.1");
  await new Promise<void>((r) => socket.once("connect", () => r()));
  socket.write(`POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:${port}\r\nContent-Type: application/json\r\n` +
    `Accept: application/json, text/event-stream\r\nMCP-Protocol-Version: ${version}\r\nMcp-Method: tools/call\r\n` +
    `Mcp-Name: chap.workspace.create\r\nContent-Length: ${body.length + (cutShort ? 500 : 0)}\r\n\r\n`);
  socket.write(body);
  await new Promise((r) => setTimeout(r, 100));
  socket.destroy();
  await new Promise((r) => setTimeout(r, 200));
}

test("a call whose body was cut short is never run, on either path", async () => {
  const all = await serve(coord, { maxBodyBytes: 4096 });
  try {
    for (const [version, era] of [[MODERN, "modern"], [HANDSHAKE, "handshake"]]) {
      await sendCreate(all.base, version, `w-cut-${era}`, true);
      assert.equal(coord.getWorkspace(`w-cut-${era}`), undefined, era);
      // The same call sent in full runs.
      await sendCreate(all.base, version, `w-whole-${era}`, false);
      assert.ok(coord.getWorkspace(`w-whole-${era}`), era);
    }
  } finally {
    await stop(all.server);
  }
});

// ------------------------------------------------------------------
// Host and Origin
// ------------------------------------------------------------------

test("a request under a name the server does not answer to is refused with 421, on either path", async () => {
  const port = new URL(base).port;
  for (const version of [MODERN, HANDSHAKE]) {
    for (const host of [`rebound.evil.example:${port}`, `localhost.:${port}`, `127.0.0.1:1`]) {
      const r = await send(list(30), { headers: { host, "mcp-protocol-version": version } });
      assert.equal(r.status, 421, `${version} ${host}`);
      assert.equal(r.body.error.code, -32600);
      assert.ok(!("id" in r.body));
    }
  }
  const upper = await send(list(31), { headers: { host: `LOCALHOST:${port}` } });
  assert.equal(upper.status, 200);
});

test("a browser request from another origin is refused with 403, and one from the server's own is served", async () => {
  const port = new URL(base).port;
  for (const origin of ["http://rebound.evil.example", "null", `http://localhost:${Number(port) + 1}`]) {
    const r = await send(list(32), { headers: { origin } });
    assert.equal(r.status, 403, origin);
    assert.equal(r.body.error.code, -32600);
  }
  for (const origin of [`http://localhost:${port}`, `https://127.0.0.1:${port}`]) {
    assert.equal((await send(list(33), { headers: { origin } })).status, 200, origin);
  }
});

test("allowedHosts and allowedOrigins add the names and origins a deployment serves under", async () => {
  const proxied = await serve(coord, { allowedHosts: ["MCP.Internal:8792"], allowedOrigins: ["http://localhost:6274/"] });
  try {
    assert.equal((await send(list(34), { to: proxied.base, headers: { host: "mcp.internal:8792" } })).status, 200);
    assert.equal((await send(list(35), { to: proxied.base, headers: { origin: "http://localhost:6274" } })).status, 200);
    assert.equal((await send(list(36), { to: proxied.base, headers: { origin: "http://localhost:6275" } })).status, 403);
  } finally {
    await stop(proxied.server);
  }
  assert.throws(() => chapMcpHttpHandler(coord, { allowedOrigins: ["localhost:6274"] }), /allowedOrigins takes origins/);
});

test("the README's example refuses the requests a rebinding page sends", async () => {
  const port = new URL(base).port;
  const r = await send(call(37, "chap.task.create", { workspace: "w", kind: "k", input: {}, assignee: "agent:b" }), {
    headers: { host: `rebound.evil.example:${port}`, origin: `http://rebound.evil.example:${port}`, "mcp-name": "chap.task.create" },
  });
  assert.equal(r.status, 421);
  assert.equal([...coord.getWorkspace("w")!.tasks.values()].filter((t) => t.kind === "k").length, 1);
});

// ------------------------------------------------------------------
// No request waits for ever
// ------------------------------------------------------------------

test("a request the server does not answer in time is answered with an internal error, on either path", async () => {
  const stuck = await serve({ dispatch: () => new Promise(() => {}) }, { timeoutMs: 200 });
  const logged: unknown[][] = [];
  const consoleError = console.error;
  console.error = (...args: unknown[]) => { logged.push(args); };
  try {
    const modern = await send(call(40, "chap.workspace.describe", { workspace: "w" }),
                              { to: stuck.base, headers: { "mcp-name": "chap.workspace.describe" } });
    assert.equal(modern.status, 500);
    assert.deepEqual(modern.body, { jsonrpc: "2.0", id: 40, error: { code: -32603, message: "Internal error" } });

    // The handshake path has begun its event stream by then. The error is
    // the answer on it, so the client hears it well before its own timeout,
    // which would reject with -32001.
    const client = new Client({ name: "legacy", version: "1.0.0" });
    await client.connect(new StreamableHTTPClientTransport(new URL(`${stuck.base}/mcp`)));
    try {
      await assert.rejects(
        client.callTool({ name: "chap.workspace.describe", arguments: { workspace: "w" } }, undefined, { timeout: 4000 }),
        (err: { code?: number }) => err.code === -32603,
      );
    } finally {
      await client.close();
    }
  } finally {
    console.error = consoleError;
    await stop(stuck.server);
  }
  assert.equal(logged.filter((args) => /had no answer within 200 ms/.test(String(args[0]))).length, 2, JSON.stringify(logged));
});

test("an error the server reports, with no answer to follow, ends the wait at once", async () => {
  // A server that reports an error from a request it will never answer, as
  // the SDK's server does with a message it cannot class.
  const handle = mcpHttpHandler({
    makeServer: () => {
      const server = new Server({ name: "failing", version: "0" }, { capabilities: { tools: {} } });
      server.setRequestHandler(ListToolsRequestSchema, () => {
        server.onerror?.(new Error("the message could not be handled"));
        return new Promise(() => {});
      });
      return server;
    },
    maxBodyBytes: 4096, timeoutMs: 60_000, allowedHosts: [], allowedOrigins: [],
  });
  const failing = createServer((req, res) => handle(req, res));
  await new Promise<void>((r) => failing.listen(0, "127.0.0.1", r));
  const consoleError = console.error;
  console.error = () => {};
  try {
    const r = await send(list(41), { to: `http://127.0.0.1:${(failing.address() as { port: number }).port}`, wait: 2000 });
    assert.equal(r.status, 500);
    assert.deepEqual(r.body, { jsonrpc: "2.0", id: 41, error: { code: -32603, message: "Internal error" } });
  } finally {
    console.error = consoleError;
    await stop(failing);
  }
});

// ------------------------------------------------------------------
// The handshake era
// ------------------------------------------------------------------

test("a handshake-era client connects, and sees the same participant and tools", async () => {
  const client = new Client({ name: "legacy", version: "1.0.0" });
  await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`)));
  try {
    const { tools } = await client.listTools();
    assert.deepEqual(tools.map((t) => t.name).sort(), [...AGENT_MCP_TOOLS].sort());
    const r = await client.callTool({ name: "chap.workspace.describe", arguments: { workspace: "w", from: "human:a" } }) as any;
    assert.equal(r.isError, true);
    assert.match(r.content[0].text, /acts as agent:b/);
    const ok = await client.callTool({ name: "chap.workspace.describe", arguments: { workspace: "w" } }) as any;
    assert.equal(JSON.parse(ok.content[0].text).id, "w");
  } finally {
    await client.close();
  }
});

test("the handshake path takes one message per POST, and POST alone", async () => {
  const all = await serve(coord, {});
  try {
    const batch = Array.from({ length: 3 }, (_, i) => ({ jsonrpc: "2.0", id: i + 1, method: "tools/call",
      params: { name: "chap.participant.join", arguments: { workspace: "w", from: `agent:batch${i}`, type: "agent" } } }));
    for (const path of ["/mcp", "/parsed"]) {
      const r = await send(batch, { to: all.base, path, headers: { "mcp-protocol-version": HANDSHAKE } });
      assert.equal(r.status, 400, path);
      assert.equal(r.body.error.code, -32600);
    }
    assert.ok(!coord.getWorkspace("w")!.members.has("agent:batch0"));

    for (const method of ["GET", "DELETE"]) {
      for (const headers of [{ "mcp-protocol-version": null }, { "mcp-protocol-version": HANDSHAKE }]) {
        const r = await send(undefined, { to: all.base, method, headers: { accept: "text/event-stream", ...headers } });
        assert.equal(r.status, 405, `${method} ${JSON.stringify(headers)}`);
        assert.equal(r.headers.allow, "POST");
      }
    }
  } finally {
    await stop(all.server);
  }
});

test("a body declaring a 2026-07-28 envelope must name that version in its header", async () => {
  for (const headers of [{ "mcp-protocol-version": HANDSHAKE }, { "mcp-protocol-version": null }]) {
    const r = await send(list(50), { headers });
    assert.equal(r.status, 400, JSON.stringify(headers));
    assert.equal(r.body.error.code, -32020);
    assert.equal(r.body.id, 50);
  }
});
