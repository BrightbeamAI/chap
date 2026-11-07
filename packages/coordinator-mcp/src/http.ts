/**
 * MCP over HTTP, for one Node `http` request and its response.
 *
 * The stdio server answers both protocol eras on one connection. Over HTTP
 * the SDK's Streamable HTTP transport answers the handshake era only: a
 * request naming MCP-Protocol-Version 2026-07-28 is refused with 400 before
 * any handler sees it. This serves both eras on one endpoint:
 *
 * - Every request is checked first. A Host that is not one of the server's
 *   names is refused with 421, so a page that rebinds its own name to this
 *   address gets nothing; an Origin naming another site is refused with 403;
 *   and any method but POST with 405, since neither era has a use for GET or
 *   DELETE on a server that keeps no session.
 * - A request with no MCP-Protocol-Version header, or one naming a
 *   handshake-era revision, goes to the SDK's transport, stateless: a
 *   server and a transport for each request. A batch is refused before it,
 *   as every revision since 2025-06-18 refuses one, and so is a body whose
 *   envelope declares a version the header does not name.
 * - A request naming any other version takes the 2026-07-28 binding: one
 *   POST carrying one JSON-RPC message, answered with one JSON object, with
 *   no session. The message must be one the SDK's own schema accepts, the
 *   headers that mirror the body are checked against it, the envelope is
 *   classified, and the request runs through the same handlers as on stdio.
 *   An error carries the HTTP status that binding gives its code.
 *
 * No request waits for ever. One the server has not answered within the
 * timeout is answered with an internal error, and one whose client has gone
 * stops waiting at once.
 */

import type { IncomingMessage, ServerResponse } from "node:http";
import type { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import {
  ErrorCode,
  SUPPORTED_PROTOCOL_VERSIONS as SDK_PROTOCOL_VERSIONS,
  isJSONRPCNotification,
  isJSONRPCRequest,
  type JSONRPCMessage,
  type JSONRPCRequest,
  type RequestId,
} from "@modelcontextprotocol/sdk/types.js";

import {
  META_CLIENT_CAPABILITIES,
  META_PROTOCOL_VERSION,
  MODERN_PROTOCOL_VERSIONS,
  UNSUPPORTED_PROTOCOL_VERSION,
} from "./envelope.js";

/** The 2026-07-28 binding's error for headers that disagree with the body, or are missing. */
const HEADER_MISMATCH = -32020;

/** The largest body read when the caller has not read it: the coordinator's 1 MiB envelope limit, and room for the MCP wrapping. */
export const DEFAULT_MAX_BODY_BYTES = 1_048_576 + 65_536;

/**
 * How long a request waits for its answer by default: longer than the 30
 * seconds a forwarding dispatcher waits for its CHAP server, so that
 * server's own error arrives first, and shorter than the 60 seconds the
 * TypeScript SDK's client waits, so the client hears why.
 */
export const DEFAULT_TIMEOUT_MS = 45_000;

/** Revisions reached through `initialize`, which the SDK's transport serves. */
const HANDSHAKE_VERSIONS: readonly string[] = SDK_PROTOCOL_VERSIONS.filter(
  (v) => !(MODERN_PROTOCOL_VERSIONS as readonly string[]).includes(v),
);

/** The methods the 2026-07-28 binding reaches here; any other is not found. */
const MODERN_METHODS = new Set(["server/discover", "tools/list", "tools/call"]);

/** The headers a 2026-07-28 request mirrors from its body; each may appear once. */
const ROUTING_HEADERS = new Set(["mcp-protocol-version", "mcp-method", "mcp-name"]);

/** HTTP status for a JSON-RPC error code on the 2026-07-28 binding. Any other code is 200. */
const STATUS_FOR_CODE = new Map<number, number>([
  [ErrorCode.ParseError, 400],
  [ErrorCode.InvalidRequest, 400],
  [ErrorCode.InvalidParams, 400],
  [HEADER_MISMATCH, 400],
  [UNSUPPORTED_PROTOCOL_VERSION, 400],
  [ErrorCode.MethodNotFound, 404],
]);

const NOT_ONE_MESSAGE = "Body must be a single JSON-RPC request or notification object";
const INTERNAL = { code: ErrorCode.InternalError, message: "Internal error" };

interface RpcError { code: number; message: string; data?: unknown }

/** What the handler is built with. */
export interface HttpSettings {
  /** The MCP server a request is answered by, one for each request. */
  makeServer: () => Server;
  maxBodyBytes: number;
  timeoutMs: number;
  /** Host header values served besides the loopback names on the port a request came in on, in lower case. */
  allowedHosts: readonly string[];
  /** Origins served besides those on a served host, each as `new URL(origin).origin` gives it. */
  allowedOrigins: readonly string[];
}

/**
 * The handler for one endpoint. `parsedBody`, when the caller has read the
 * body already, is used as it stands.
 */
export function mcpHttpHandler(settings: HttpSettings) {
  return async function handle(req: IncomingMessage, res: ServerResponse, parsedBody?: unknown): Promise<void> {
    try {
      const hosts = servedHosts(req, settings.allowedHosts);
      if (!hosts.has((req.headers.host ?? "").toLowerCase())) {
        answer(res, 421, { code: ErrorCode.InvalidRequest, message: "Unknown host: add it to allowedHosts to serve under that name" });
        return;
      }
      if (foreignOrigin(req.headers.origin, hosts, settings.allowedOrigins)) {
        answer(res, 403, { code: ErrorCode.InvalidRequest, message: "Cross-origin requests are refused: add the origin to allowedOrigins to serve it" });
        return;
      }
      if (req.method !== "POST") {
        res.writeHead(405, { allow: "POST" }).end();
        return;
      }
      const declared = req.headers["mcp-protocol-version"];
      if (declared === undefined || (typeof declared === "string" && HANDSHAKE_VERSIONS.includes(declared))) {
        await handshake(settings, req, res, parsedBody);
        return;
      }
      await singleExchange(settings, req, res, parsedBody);
    } catch (err) {
      // The handler's promise settles whatever happens, so a server that
      // mounts it without a catch of its own keeps running. The detail goes
      // to the log, and the client gets a short answer if it can still take
      // one.
      console.error("MCP request failed:", err);
      if (!res.headersSent) answer(res, 500, INTERNAL);
      else res.destroy();
    }
  };
}

/**
 * A handshake-era request: the SDK's transport answers it, once the body is
 * known to be one message whose envelope, if it declares a version, declares
 * the one the header names.
 */
async function handshake(settings: HttpSettings, req: IncomingMessage, res: ServerResponse, parsedBody: unknown): Promise<void> {
  let body = parsedBody;
  // A body of another type is left unread, for the transport to refuse with 415.
  if (body === undefined && isJsonType(req.headers["content-type"])) {
    const read = await readJson(req, res, settings.maxBodyBytes);
    if (!read) return;
    body = read.value;
  }
  if (Array.isArray(body)) {
    answer(res, 400, { code: ErrorCode.InvalidRequest, message: "A batch is not accepted: send each JSON-RPC message in a POST of its own" });
    return;
  }
  const header = req.headers["mcp-protocol-version"];
  const version = envelopeVersion(body);
  if (version !== undefined && version !== header) {
    answer(res, 400, {
      code: HEADER_MISMATCH,
      message: header === undefined
        ? "mcp-protocol-version header is missing, and the request envelope declares a protocol version"
        : "mcp-protocol-version header does not match the request envelope's protocol version",
    }, idOf(body));
    return;
  }

  const id = isJSONRPCRequest(body) ? body.id : undefined;
  const server = settings.makeServer();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, maxRequestBodySize: settings.maxBodyBytes });
  const timer = setTimeout(() => {
    console.error(`MCP request ${JSON.stringify(id ?? null)} had no answer within ${settings.timeoutMs} ms`);
    if (!res.headersSent) answer(res, 500, INTERNAL, id);
    // The transport has begun its event stream: the error is the answer on
    // it, and ends it.
    else if (id !== undefined) transport.send({ jsonrpc: "2.0", id, error: INTERNAL }).catch(() => res.destroy());
    else res.destroy();
  }, settings.timeoutMs);
  res.on("close", () => { clearTimeout(timer); void transport.close(); void server.close(); });
  await server.connect(transport);
  await transport.handleRequest(req, res, body);
}

/** One 2026-07-28 request: checked, run, and answered with one JSON object. */
async function singleExchange(settings: HttpSettings, req: IncomingMessage, res: ServerResponse, parsedBody: unknown): Promise<void> {
  if (!acceptsJson(req.headers.accept)) {
    res.writeHead(406).end();
    return;
  }
  if (!isJsonType(req.headers["content-type"])) {
    answer(res, 415, { code: ErrorCode.InvalidRequest, message: "Unsupported Media Type: Content-Type must be application/json" });
    return;
  }

  let body = parsedBody;
  if (body === undefined) {
    const read = await readJson(req, res, settings.maxBodyBytes);
    if (!read) return;
    body = read.value;
  }

  // A notification: accepted and dropped. This binding defines none that a
  // client sends, and a single exchange has nothing for one to act on.
  if (isObject(body) && !("id" in body)) {
    if (!isJSONRPCNotification(body)) {
      answer(res, 400, { code: ErrorCode.InvalidRequest, message: NOT_ONE_MESSAGE });
      return;
    }
    const version = String(req.headers["mcp-protocol-version"]);
    if (!(MODERN_PROTOCOL_VERSIONS as readonly string[]).includes(version)) {
      answer(res, 400, unsupported(version));
      return;
    }
    res.writeHead(202).end();
    return;
  }
  // The SDK's server acts only on a message its schema accepts, and never
  // answers one it cannot class, so the same schema decides here.
  if (!isJSONRPCRequest(body)) {
    answer(res, 400, { code: ErrorCode.InvalidRequest, message: NOT_ONE_MESSAGE }, idOf(body));
    return;
  }

  const refusal = duplicatedHeader(req.rawHeaders) ?? classify(body, req.headers);
  if (refusal) {
    answer(res, STATUS_FOR_CODE.get(refusal.code) ?? 200, refusal, body.id);
    return;
  }
  if (!MODERN_METHODS.has(body.method)) {
    answer(res, 404, { code: ErrorCode.MethodNotFound, message: "Method not found", data: body.method }, body.id);
    return;
  }
  if (!validParams(body)) {
    answer(res, 400, { code: ErrorCode.InvalidParams, message: "Invalid request parameters" }, body.id);
    return;
  }

  const outcome = await exchange(settings, res, body);
  switch (outcome.kind) {
    case "reply": {
      const error = (outcome.message as { error?: RpcError }).error;
      const status = error ? STATUS_FOR_CODE.get(error.code) ?? 200 : 200;
      res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(outcome.message));
      return;
    }
    case "gone":
      return;
    case "timeout":
      console.error(`MCP request ${JSON.stringify(body.id)} had no answer within ${settings.timeoutMs} ms`);
      answer(res, 500, INTERNAL, body.id);
      return;
    case "error":
      console.error("MCP request failed:", outcome.error);
      answer(res, 500, INTERNAL, body.id);
  }
}

type Outcome =
  | { kind: "reply"; message: JSONRPCMessage }
  | { kind: "error"; error: Error }
  | { kind: "timeout" }
  | { kind: "gone" };

/**
 * Run one request through a server of its own, and wait for the first of:
 * its answer, an error the server reports with no answer to follow, the
 * timeout, or the client closing the connection.
 */
async function exchange(settings: HttpSettings, res: ServerResponse, request: JSONRPCRequest): Promise<Outcome> {
  let settle!: (outcome: Outcome) => void;
  const outcome = new Promise<Outcome>((resolve) => { settle = resolve; });
  const timer = setTimeout(() => settle({ kind: "timeout" }), settings.timeoutMs);
  const gone = () => settle({ kind: "gone" });
  res.once("close", gone);

  const server = settings.makeServer();
  server.onerror = (error) => settle({ kind: "error", error });
  const transport = new SingleExchangeTransport((message) => settle({ kind: "reply", message }));
  try {
    await server.connect(transport);
    transport.onmessage?.(request);
    return await outcome;
  } finally {
    clearTimeout(timer);
    res.off("close", gone);
    await server.close();
  }
}

/**
 * The 2026-07-28 ladder for a request that reached this path, first failure
 * first: the envelope is present, the mirrored headers match the body, and
 * the version is one this server implements.
 */
function classify(body: JSONRPCRequest, headers: IncomingMessage["headers"]): RpcError | null {
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
  // tools/call is the one method served here that names what Mcp-Name mirrors.
  const named = body.method === "tools/call" && isObject(body.params) ? body.params.name : undefined;
  if (named !== undefined && named !== null && decodeHeaderValue(headers["mcp-name"]) !== named) {
    return { code: HEADER_MISMATCH, message: "mcp-name header does not match the request body's 'name' parameter" };
  }
  if (!(MODERN_PROTOCOL_VERSIONS as readonly string[]).includes(version as string)) return unsupported(String(version));
  return null;
}

/**
 * The params of a method served here have the shape it takes: a tool call
 * names its tool and passes an object of arguments, and a list takes a
 * string cursor. The SDK would refuse others with its own validation text.
 */
function validParams(body: JSONRPCRequest): boolean {
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

/** The protocol version a body's envelope declares, if it declares one, as classifyEnvelope reads it. */
function envelopeVersion(body: unknown): unknown {
  const meta = isObject(body) && isObject(body.params) ? body.params._meta : undefined;
  const version = isObject(meta) ? meta[META_PROTOCOL_VERSION] : undefined;
  return version === null ? undefined : version;
}

/** The names a request may reach the server by: its loopback names on the port the request came in on, and those it was given. */
function servedHosts(req: IncomingMessage, extra: readonly string[]): Set<string> {
  const port = req.socket.localPort;
  return new Set([`127.0.0.1:${port}`, `localhost:${port}`, `[::1]:${port}`, ...extra]);
}

/**
 * An Origin naming a site other than the server's own names and the origins
 * it was given; none at all is a client that is not a browser.
 */
function foreignOrigin(origin: string | undefined, hosts: Set<string>, origins: readonly string[]): boolean {
  if (origin === undefined) return false;
  let url: URL;
  try { url = new URL(origin); } catch { return true; }
  return !(hosts.has(url.host.toLowerCase()) || origins.includes(url.origin));
}

/** Accept names JSON, directly or by a wildcard. */
function acceptsJson(accept: string | undefined): boolean {
  return (accept ?? "").split(",").some((part) => {
    const type = part.split(";")[0].trim().toLowerCase();
    return type === "application/json" || type === "application/*" || type === "*/*";
  });
}

function isJsonType(contentType: string | undefined): boolean {
  return (contentType ?? "").toLowerCase().startsWith("application/json");
}

/**
 * A JSON-RPC error. `id` is left out where the request's could not be read,
 * as the 2026-07-28 schema has it: an id is a string or a number, never null.
 */
function answer(res: ServerResponse, status: number, error: RpcError, id?: RequestId): void {
  res.writeHead(status, { "content-type": "application/json" })
    .end(JSON.stringify({ jsonrpc: "2.0", ...(id !== undefined ? { id } : {}), error }));
}

/** The body's id, where it has one a response can carry. */
function idOf(body: unknown): RequestId | undefined {
  if (!isObject(body)) return undefined;
  const id = body.id;
  return typeof id === "string" || (typeof id === "number" && Number.isInteger(id)) ? id : undefined;
}

class BodyTooLarge extends Error {}

/**
 * The body, read up to `limit` bytes and parsed. Undefined when it has been
 * answered here: 413 over the limit, 400 when it is not JSON, and nothing
 * at all when the client went away before its body arrived, so a call cut
 * short never runs.
 */
async function readJson(req: IncomingMessage, res: ServerResponse, limit: number): Promise<{ value: unknown } | undefined> {
  let text: string;
  try {
    text = await readBody(req, limit);
  } catch (err) {
    if (err instanceof BodyTooLarge) {
      answer(res, 413, { code: ErrorCode.InvalidRequest, message: `Payload Too Large: Request body must not exceed ${limit} bytes` });
    } else {
      res.destroy();
    }
    return undefined;
  }
  try {
    return { value: JSON.parse(text) };
  } catch {
    answer(res, 400, { code: ErrorCode.ParseError, message: "Parse error" });
    return undefined;
  }
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

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

  constructor(private readonly onreply: (message: JSONRPCMessage) => void) {}

  async start(): Promise<void> {}

  async send(message: JSONRPCMessage): Promise<void> {
    if ("id" in message && ("result" in message || "error" in message)) this.onreply(message);
  }

  async close(): Promise<void> {
    this.onclose?.();
  }
}
