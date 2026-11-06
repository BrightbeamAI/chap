/**
 * MCP over HTTP, for one Node `http` request and its response.
 *
 * The stdio server answers both protocol eras on one connection. Over HTTP
 * the SDK's Streamable HTTP transport answers the handshake era only: a
 * request naming MCP-Protocol-Version 2026-07-28 is refused with 400 before
 * any handler sees it. This serves both eras on one endpoint:
 *
 * - A request with no MCP-Protocol-Version header, or one naming a
 *   handshake-era revision, goes to the SDK's transport, stateless: a
 *   server and a transport for each request.
 * - A request naming any other version takes the 2026-07-28 binding: one
 *   POST carrying one JSON-RPC message, answered with one JSON object, with
 *   no session. The headers that mirror the body are checked against it,
 *   the envelope is classified, and the request runs through the same
 *   handlers as on stdio. An error carries the HTTP status that binding
 *   gives its code.
 *
 * Neither path checks Host or Origin: a server mounts this behind the checks
 * it applies to every route, as the templates' servers do.
 */

import type { IncomingMessage, ServerResponse } from "node:http";
import type { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import {
  ErrorCode,
  SUPPORTED_PROTOCOL_VERSIONS as SDK_PROTOCOL_VERSIONS,
  type JSONRPCMessage,
} from "@modelcontextprotocol/sdk/types.js";

import {
  META_CLIENT_CAPABILITIES,
  META_PROTOCOL_VERSION,
  MODERN_PROTOCOL_VERSIONS,
  UNSUPPORTED_PROTOCOL_VERSION,
} from "./envelope.js";

/** JSON-RPC's implementation-defined server error, which the SDK's transport sends with a 413 or a 415. */
const SERVER_ERROR = -32000;
/** The 2026-07-28 binding's error for headers that disagree with the body, or are missing. */
const HEADER_MISMATCH = -32020;
/** The 2026-07-28 error for a request that needs a client capability the client did not declare. */
const MISSING_REQUIRED_CLIENT_CAPABILITY = -32021;

/** The largest body read when the caller has not read it: the coordinator's 1 MiB envelope limit, and room for the MCP wrapping. */
export const DEFAULT_MAX_BODY_BYTES = 1_048_576 + 65_536;

/** Revisions reached through `initialize`, which the SDK's transport serves. */
const HANDSHAKE_VERSIONS: readonly string[] = SDK_PROTOCOL_VERSIONS.filter(
  (v) => !(MODERN_PROTOCOL_VERSIONS as readonly string[]).includes(v),
);

/** The methods the 2026-07-28 binding reaches here; any other is not found. */
const MODERN_METHODS = new Set(["server/discover", "tools/list", "tools/call"]);

/** Methods whose body names a resource that the Mcp-Name header mirrors, and the field it is in. */
const NAME_BEARING: Record<string, string> = { "tools/call": "name", "prompts/get": "name", "resources/read": "uri" };

/** The headers a 2026-07-28 request mirrors from its body; each may appear once. */
const ROUTING_HEADERS = new Set(["mcp-protocol-version", "mcp-method", "mcp-name"]);

/** HTTP status for a JSON-RPC error code on the 2026-07-28 binding. Any other code is 200. */
const STATUS_FOR_CODE = new Map<number, number>([
  [ErrorCode.ParseError, 400],
  [ErrorCode.InvalidRequest, 400],
  [ErrorCode.InvalidParams, 400],
  [HEADER_MISMATCH, 400],
  [MISSING_REQUIRED_CLIENT_CAPABILITY, 400],
  [UNSUPPORTED_PROTOCOL_VERSION, 400],
  [ErrorCode.MethodNotFound, 404],
]);

interface RpcError { code: number; message: string; data?: unknown }

class BodyTooLarge extends Error {}

/**
 * The handler for one endpoint. `makeServer` builds the MCP server a request
 * is answered by; `parsedBody`, when the caller has read the body already, is
 * used as it stands.
 */
export function mcpHttpHandler(makeServer: () => Server, maxBodyBytes: number) {
  return async function handle(req: IncomingMessage, res: ServerResponse, parsedBody?: unknown): Promise<void> {
    try {
      const declared = req.headers["mcp-protocol-version"];
      if (declared === undefined || (typeof declared === "string" && HANDSHAKE_VERSIONS.includes(declared))) {
        const server = makeServer();
        const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, maxRequestBodySize: maxBodyBytes });
        res.on("close", () => { void transport.close(); void server.close(); });
        await server.connect(transport);
        await transport.handleRequest(req, res, parsedBody);
        return;
      }
      await singleExchange(makeServer, maxBodyBytes, req, res, parsedBody);
    } catch (err) {
      // The handler's promise settles whatever happens, so a server that
      // mounts it without a catch of its own keeps running. The detail goes
      // to the log, and the client gets a short answer if it can still take
      // one.
      console.error("MCP request failed:", err);
      if (!res.headersSent) answer(res, 500, null, { code: ErrorCode.InternalError, message: "Internal error" });
      else res.destroy();
    }
  };
}

/** One 2026-07-28 request: checked, run, and answered with one JSON object. */
async function singleExchange(
  makeServer: () => Server,
  maxBodyBytes: number,
  req: IncomingMessage,
  res: ServerResponse,
  parsedBody: unknown,
): Promise<void> {
  if (req.method !== "POST") {
    res.writeHead(405, { allow: "POST" }).end();
    return;
  }
  if (!acceptsJson(req.headers.accept)) {
    res.writeHead(406).end();
    return;
  }
  if (!(req.headers["content-type"] ?? "").toLowerCase().startsWith("application/json")) {
    answer(res, 415, null, { code: SERVER_ERROR, message: "Unsupported Media Type: Content-Type must be application/json" });
    return;
  }

  let body = parsedBody;
  if (body === undefined) {
    let text: string;
    try {
      text = await readBody(req, maxBodyBytes);
    } catch (err) {
      if (err instanceof BodyTooLarge) {
        answer(res, 413, null, { code: SERVER_ERROR, message: `Payload Too Large: Request body must not exceed ${maxBodyBytes} bytes` });
      } else {
        // The client went away before its body arrived: no one is left to answer.
        res.destroy();
      }
      return;
    }
    try {
      body = JSON.parse(text);
    } catch {
      answer(res, 400, null, { code: ErrorCode.ParseError, message: "Parse error" });
      return;
    }
  }

  // A notification: accepted and dropped. This binding defines none that a
  // client sends, and a single exchange has nothing for one to act on.
  if (isObject(body) && !("id" in body)) {
    if (body.jsonrpc !== "2.0" || typeof body.method !== "string" || (body.params !== undefined && !isObject(body.params))) {
      answer(res, 400, null, { code: ErrorCode.InvalidRequest, message: "Body must be a single JSON-RPC request or notification object" });
      return;
    }
    const version = String(req.headers["mcp-protocol-version"]);
    if (!(MODERN_PROTOCOL_VERSIONS as readonly string[]).includes(version)) {
      answer(res, 400, null, unsupported(version));
      return;
    }
    res.writeHead(202).end();
    return;
  }
  if (!isRequest(body)) {
    answer(res, 400, null, { code: ErrorCode.InvalidRequest, message: "Body must be a single JSON-RPC request or notification object" });
    return;
  }

  const refusal = duplicatedHeader(req.rawHeaders) ?? classify(body, req.headers);
  if (refusal) {
    answer(res, STATUS_FOR_CODE.get(refusal.code) ?? 200, body.id, refusal);
    return;
  }
  if (!MODERN_METHODS.has(body.method)) {
    answer(res, 404, body.id, { code: ErrorCode.MethodNotFound, message: "Method not found", data: body.method });
    return;
  }
  if (!validParams(body)) {
    answer(res, 400, body.id, { code: ErrorCode.InvalidParams, message: "Invalid request parameters" });
    return;
  }

  const server = makeServer();
  const exchange = new SingleExchangeTransport();
  await server.connect(exchange);
  try {
    exchange.onmessage?.(body as JSONRPCMessage);
    const reply = await exchange.reply;
    const error = (reply as { error?: RpcError }).error;
    const status = error ? STATUS_FOR_CODE.get(error.code) ?? 200 : 200;
    res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(reply));
  } finally {
    await server.close();
  }
}

/**
 * The 2026-07-28 ladder for a request that reached this path, first failure
 * first: the envelope is present, the mirrored headers match the body, and
 * the version is one this server implements.
 */
function classify(body: Request, headers: IncomingMessage["headers"]): RpcError | null {
  const meta = isObject(body.params) ? body.params._meta : undefined;
  if (!isObject(meta)) {
    return {
      code: ErrorCode.InvalidParams,
      message: `params._meta must be an object carrying the required '${META_PROTOCOL_VERSION}' and '${META_CLIENT_CAPABILITIES}' envelope keys`,
    };
  }
  const missing = [META_PROTOCOL_VERSION, META_CLIENT_CAPABILITIES].filter((key) => !(key in meta));
  if (missing.length) {
    return { code: ErrorCode.InvalidParams, message: `params._meta is missing the required envelope key(s): ${missing.join(", ")}` };
  }
  const version = meta[META_PROTOCOL_VERSION];
  if (headers["mcp-protocol-version"] !== version) {
    return { code: HEADER_MISMATCH, message: "mcp-protocol-version header does not match the request envelope's protocol version" };
  }
  if (headers["mcp-method"] !== body.method) {
    return { code: HEADER_MISMATCH, message: "mcp-method header does not match the request body's method" };
  }
  const nameKey = NAME_BEARING[body.method];
  const named = nameKey && isObject(body.params) ? body.params[nameKey] : undefined;
  if (named !== undefined && named !== null && decodeHeaderValue(headers["mcp-name"]) !== named) {
    return { code: HEADER_MISMATCH, message: `mcp-name header does not match the request body's '${nameKey}' parameter` };
  }
  if (!(MODERN_PROTOCOL_VERSIONS as readonly string[]).includes(version as string)) return unsupported(String(version));
  return null;
}

/**
 * The params of a method served here have the shape it takes: a tool call
 * names its tool and passes an object of arguments, and a list takes a
 * string cursor. The SDK would refuse others with its own validation text.
 */
function validParams(body: Request): boolean {
  const params = body.params ?? {};
  if (body.method === "tools/call") {
    return typeof params.name === "string" && (params.arguments === undefined || isObject(params.arguments));
  }
  if (body.method === "tools/list") return params.cursor === undefined || typeof params.cursor === "string";
  return true;
}

function unsupported(requested: string): RpcError {
  return { code: UNSUPPORTED_PROTOCOL_VERSION, message: "Unsupported protocol version", data: { supported: [...MODERN_PROTOCOL_VERSIONS], requested } };
}

/** A routing header sent more than once: readers taking the first and the last copy would disagree. */
function duplicatedHeader(rawHeaders: string[]): RpcError | null {
  const seen = new Set<string>();
  for (let i = 0; i < rawHeaders.length; i += 2) {
    const name = rawHeaders[i].toLowerCase();
    if (!ROUTING_HEADERS.has(name)) continue;
    if (seen.has(name)) return { code: HEADER_MISMATCH, message: `${name} header appears more than once` };
    seen.add(name);
  }
  return null;
}

/**
 * A header value as the client meant it. A value that would not survive as
 * plain ASCII travels as `=?base64?<UTF-8 in base64>?=`; one that does not
 * decode, or is not in canonical form, reads as absent, so it matches nothing.
 */
function decodeHeaderValue(value: string | string[] | undefined): string | undefined {
  if (typeof value !== "string") return undefined;
  const m = /^=\?base64\?(.*)\?=$/.exec(value);
  if (!m) return value;
  const bytes = Buffer.from(m[1], "base64");
  if (bytes.toString("base64") !== m[1]) return undefined;
  const text = new TextDecoder("utf-8", { fatal: true });
  try { return text.decode(bytes); } catch { return undefined; }
}

/** Accept names JSON, directly or by a wildcard. */
function acceptsJson(accept: string | undefined): boolean {
  return (accept ?? "").split(",").some((part) => {
    const type = part.split(";")[0].trim().toLowerCase();
    return type === "application/json" || type === "application/*" || type === "*/*";
  });
}

function answer(res: ServerResponse, status: number, id: unknown, error: RpcError): void {
  res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify({ jsonrpc: "2.0", id: id ?? null, error }));
}

async function readBody(req: IncomingMessage, limit: number): Promise<string> {
  if (Number(req.headers["content-length"]) > limit) throw new BodyTooLarge();
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > limit) throw new BodyTooLarge();
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

interface Request { jsonrpc: "2.0"; id: string | number; method: string; params?: Record<string, unknown> }

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRequest(body: unknown): body is Request {
  return isObject(body) && body.jsonrpc === "2.0" && typeof body.method === "string"
    && (typeof body.id === "string" || (typeof body.id === "number" && Number.isInteger(body.id)))
    && (body.params === undefined || isObject(body.params));
}

/**
 * A transport for one request: the server's handlers receive it, and the
 * first response they send is the answer. A notification the server sends
 * on the way is dropped, since the answer is one JSON object.
 */
class SingleExchangeTransport implements Transport {
  onclose?: () => void;
  onerror?: (error: Error) => void;
  onmessage?: (message: JSONRPCMessage) => void;
  private settle!: (message: JSONRPCMessage) => void;
  readonly reply = new Promise<JSONRPCMessage>((resolve) => { this.settle = resolve; });

  async start(): Promise<void> {}

  async send(message: JSONRPCMessage): Promise<void> {
    if ("id" in message && ("result" in message || "error" in message)) this.settle(message);
  }

  async close(): Promise<void> {
    this.onclose?.();
  }
}
