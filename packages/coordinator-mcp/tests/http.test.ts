/**
 * MCP over HTTP, in both protocol eras, on one endpoint.
 *
 * The templates served /mcp with the SDK's Streamable HTTP transport, which
 * refuses a request naming MCP-Protocol-Version 2026-07-28 with 400 before
 * any handler sees it; the stdio server answers that revision.
 * chapMcpHttpHandler answers it as the revision's single exchange, and
 * hands the handshake era to the SDK's transport, with the participant and
 * tool set holding on both paths.
 *
 * The 2026-07-28 answers pinned here are the Python `mcp` SDK's to the same
 * requests, in status, error code and message, with one exception: a POST
 * that is not JSON is refused with 415, as the TypeScript SDK's transport
 * refuses it on the handshake path, where the Python SDK answers 400.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer, request, type Server as HttpServer } from "node:http";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import {
  AGENT_MCP_TOOLS,
  META_CLIENT_CAPABILITIES,
  META_PROTOCOL_VERSION,
  chapMcpHttpHandler,
} from "../src/index.js";

const MODERN = "2026-07-28";
const META = { [META_PROTOCOL_VERSION]: MODERN, [META_CLIENT_CAPABILITIES]: {} };

let coord: Coordinator;
let http: HttpServer;
let base: string;

before(async () => {
  coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0", "review/1.0"] });
  for (const [method, params] of [
    ["workspace.create", { from: "human:a" }],
    ["participant.join", { from: "human:a", type: "human" }],
    ["participant.join", { from: "agent:b", type: "agent" }],
  ] as const) {
    coord.dispatch({ jsonrpc: "2.0", id: method, method, params: { workspace: "w", ...params } });
  }
  const mcp = chapMcpHttpHandler(coord, {
    participant: "agent:b",
    toolFilter: (name) => AGENT_MCP_TOOLS.has(name),
    maxBodyBytes: 4096,
  });
  // /parsed reads and parses the body first, as the templates' server does.
  http = createServer(async (req, res) => {
    if (req.url === "/parsed" && req.method === "POST") {
      let text = "";
      for await (const chunk of req) text += chunk;
      return mcp(req, res, JSON.parse(text));
    }
    return mcp(req, res);
  });
  await new Promise<void>((r) => http.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${(http.address() as { port: number }).port}`;
});

after(() => new Promise<void>((r) => http.close(() => r())));

interface Sent { status: number; headers: Record<string, string | string[] | undefined>; body: any }

/** POST (or another method) to the endpoint, with the 2026-07-28 headers unless told otherwise. */
function send(
  body: unknown,
  { method = "POST", path = "/mcp", headers = {} as Record<string, string | string[] | null>, raw }: {
    method?: string; path?: string; headers?: Record<string, string | string[] | null>; raw?: string;
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
    const r = request(base + path, { method }, (res) => {
      let text = "";
      res.on("data", (c) => { text += c; });
      res.on("end", () => resolve({ status: res.statusCode!, headers: res.headers, body: text ? JSON.parse(text) : null }));
    });
    r.on("error", reject);
    for (const [name, value] of Object.entries(all)) if (value !== null) r.setHeader(name, value);
    r.end(raw ?? (body === undefined ? undefined : JSON.stringify(body)));
  });
}

const call = (id: number, name: string, args: Record<string, unknown>) =>
  ({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args, _meta: META } });

// ------------------------------------------------------------------
// The 2026-07-28 single exchange
// ------------------------------------------------------------------

test("a 2026-07-28 tools/list is answered with one JSON object, and the agent's tools", async () => {
  const r = await send({ jsonrpc: "2.0", id: 1, method: "tools/list", params: { _meta: META } });
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
  const r = await send({ jsonrpc: "2.0", id: 6, method: "tools/list", params: { _meta: META } }, { path: "/parsed" });
  assert.equal(r.status, 200);
  assert.equal(r.body.result.tools.length, AGENT_MCP_TOOLS.size);
});

test("the headers must match the body", async () => {
  const list = { jsonrpc: "2.0", id: 7, method: "tools/list", params: { _meta: META } };
  const wrongMethod = await send(list, { headers: { "mcp-method": "tools/call" } });
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

  const twice = await send(list, { headers: { "mcp-method": ["tools/list", "tools/list"] } });
  assert.equal(twice.status, 400);
  assert.match(twice.body.error.message, /mcp-method header appears more than once/);

  const otherVersion = await send({ ...list, params: { _meta: { ...META, [META_PROTOCOL_VERSION]: "2099-01-01" } } });
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
  for (const method of ["initialize", "ping", "resources/list"]) {
    const r = await send({ jsonrpc: "2.0", id: 12, method, params: { _meta: META } });
    assert.equal(r.status, 404, method);
    assert.deepEqual(r.body.error, { code: -32601, message: "Method not found", data: method });
  }
});

test("a notification is accepted with 202 and no body", async () => {
  const r = await send({ jsonrpc: "2.0", method: "notifications/cancelled", params: {} });
  assert.equal(r.status, 202);
  assert.equal(r.body, null);
  const malformed = await send({ jsonrpc: "2.0", method: "notifications/cancelled", params: 5 });
  assert.equal(malformed.status, 400);
  assert.equal(malformed.body.error.code, -32600);
});

test("a tool call without a tool name, or with arguments that are not an object, is invalid params", async () => {
  for (const params of [{ arguments: {} }, { name: "chap.workspace.describe", arguments: [1] }]) {
    const r = await send({ jsonrpc: "2.0", id: 14, method: "tools/call", params: { ...params, _meta: META } },
                         { headers: { "mcp-name": "chap.workspace.describe" } });
    assert.equal(r.status, 400, JSON.stringify(params));
    assert.deepEqual(r.body.error, { code: -32602, message: "Invalid request parameters" });
  }
});

test("a client that goes away mid-body leaves the server running", async () => {
  await new Promise<void>((resolve) => {
    const r = request(`${base}/mcp`, { method: "POST" });
    for (const [name, value] of Object.entries({
      "content-type": "application/json", accept: "application/json", "mcp-protocol-version": MODERN,
      "mcp-method": "tools/list", "content-length": "1000",
    })) r.setHeader(name, value);
    r.on("error", () => resolve());
    r.write("{\"jsonrpc\":");
    setTimeout(() => { r.destroy(); resolve(); }, 50);
  });
  await new Promise((r) => setTimeout(r, 50));
  const after = await send({ jsonrpc: "2.0", id: 15, method: "tools/list", params: { _meta: META } });
  assert.equal(after.status, 200);
});

test("what is not one request or notification is refused", async () => {
  const batch = await send([{ jsonrpc: "2.0", id: 1, method: "tools/list" }], { headers: { "mcp-method": "tools/list" } });
  assert.equal(batch.status, 400);
  assert.equal(batch.body.error.code, -32600);
  assert.equal(batch.body.id, null);

  const garbled = await send(null, { raw: "{not json", headers: { "mcp-method": "tools/list" } });
  assert.equal(garbled.status, 400);
  assert.deepEqual(garbled.body.error, { code: -32700, message: "Parse error" });
});

test("the binding's HTTP rules: POST only, JSON accepted and sent, and a size limit", async () => {
  const get = await send(undefined, { method: "GET" });
  assert.equal(get.status, 405);
  assert.equal(get.headers.allow, "POST");

  const list = { jsonrpc: "2.0", id: 13, method: "tools/list", params: { _meta: META } };
  assert.equal((await send(list, { headers: { accept: "text/html" } })).status, 406);
  assert.equal((await send(list, { headers: { "content-type": "text/plain" } })).status, 415);

  const big = await send({ ...list, params: { _meta: META, pad: "x".repeat(8192) } });
  assert.equal(big.status, 413);
  assert.match(big.body.error.message, /must not exceed 4096 bytes/);
});

// ------------------------------------------------------------------
// The handshake era, through the SDK's own client
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
