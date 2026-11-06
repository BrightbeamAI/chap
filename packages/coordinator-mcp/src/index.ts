/**
 * @brightbeamai/chap-coordinator-mcp
 *
 * MCP server adapter for a CHAP Coordinator. Wraps a Coordinator
 * instance and exposes every CHAP method as an MCP tool.
 *
 * Spec target: MCP 2026-07-28 (current), serving MCP 2025-11-25
 * clients as well. CHAP 0.3.
 *
 * Usage (stdio):
 *
 *   import { Coordinator } from "@brightbeamai/chap-coordinator";
 *   import { makeChapMcpServer } from "@brightbeamai/chap-coordinator-mcp";
 *   import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
 *
 *   const coord = new Coordinator({ ... });
 *   const server = makeChapMcpServer(coord, { name: "chap", version: "0.3.4" });
 *   await server.connect(new StdioServerTransport());
 *
 * Usage (HTTP): chapMcpHttpHandler(coord, options) answers a Node `http`
 * request on an MCP endpoint, in both protocol eras.
 *
 * Architecture notes:
 *
 * - One Coordinator -> one MCP server. Multi-workspace is handled
 *   inside the Coordinator (workspaces are addressable by id).
 *
 * - The adapter holds no state. Every tool call translates to a
 *   JSON-RPC envelope and dispatches through coord.dispatch().
 *
 * - Tool naming follows "chap.<method>" so the prefix avoids
 *   collisions with other MCP servers a client might load.
 *
 * - A server that is one participant's connection, such as an
 *   agent's, takes `participant`: every call is made as that member,
 *   and a call naming another `from` is refused. AGENT_MCP_TOOLS is
 *   the tool set such a connection offers an agent.
 *
 * - Tool inputs are described by JSON Schemas (not Zod) and are
 *   passed through to the Coordinator without re-validating at the
 *   MCP layer. The Coordinator's own dispatch validates params and
 *   returns spec-correct JSON-RPC error codes, which we surface as
 *   MCP tool errors. This keeps the schema definitions single-sourced.
 *
 * - Authentication is intentionally out of scope at this layer.
 *   Apply OAuth 2.1 / Streamable HTTP auth at the transport layer
 *   per MCP's auth model.
 */

import type { IncomingMessage, ServerResponse } from "node:http";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ErrorCode,
  type CallToolResult,
  type Tool,
} from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import type { Coordinator, Envelope } from "@brightbeamai/chap-coordinator";

import { SCHEMAS, TOOL_NAMES, methodForTool, coerceToolArgs, type JsonSchema } from "./schemas.js";
import { TOOL_DESCRIPTIONS, TOOL_ANNOTATIONS } from "./tools.js";
import { classifyEnvelope, ProtocolError, SUPPORTED_PROTOCOL_VERSIONS, type ProtocolEra } from "./envelope.js";
import { ChapEndpointError } from "./remote.js";
import { DEFAULT_MAX_BODY_BYTES, mcpHttpHandler } from "./http.js";

export { SCHEMAS, TOOL_NAMES, schemaFor, methodForTool, coerceToolArgs } from "./schemas.js";
export { TOOL_DESCRIPTIONS, TOOL_ANNOTATIONS, AGENT_MCP_TOOLS } from "./tools.js";
export type { ToolAnnotations } from "./tools.js";
export type { JsonSchema } from "./schemas.js";

export {
  ProtocolError,
  MODERN_PROTOCOL_VERSIONS,
  SUPPORTED_PROTOCOL_VERSIONS,
  UNSUPPORTED_PROTOCOL_VERSION,
  META_PROTOCOL_VERSION,
  META_CLIENT_INFO,
  META_CLIENT_CAPABILITIES,
  classifyEnvelope,
} from "./envelope.js";
export type { ProtocolEra } from "./envelope.js";

/** `_meta` key carrying server identity (MCP 2026-07-28, SEP-2575). */
export const META_SERVER_INFO = "io.modelcontextprotocol/serverInfo";

/** Freshness hint for cacheable list results. The CHAP tool surface is
 *  fixed at construction, so a long TTL is safe. */
const LIST_TTL_MS = 3_600_000;

/**
 * Request schema for `server/discover` (MCP 2026-07-28, SEP-2575).
 *
 * Hand-written because the pinned SDK major predates the 2026 era. The
 * shape is permissive on `params` so a probe carrying `_meta`
 * (protocolVersion, clientInfo, clientCapabilities) validates, and a
 * bare probe with no params validates too.
 */
export const ServerDiscoverRequestSchema = z.object({
  method: z.literal("server/discover"),
  // `.passthrough()` keeps unknown keys under zod 3 and zod 4 alike, and the
  // package accepts both majors. The zod 4 name for the same thing does not
  // exist in zod 3, where it stopped the package at import.
  params: z.optional(z.object({ _meta: z.optional(z.record(z.string(), z.unknown())) }).passthrough()),
});

/** JSON-RPC's internal error: a fault inside the coordinator, which refused nothing. */
const INTERNAL_ERROR = -32603;

/** What a client is told of a fault inside the server; the detail is in the server's log. */
const FAILED_INSIDE = { chap_error: INTERNAL_ERROR, message: "The CHAP call failed inside the server; its log has the detail." };

/**
 * What the server dispatches each call through: a Coordinator, or anything
 * with the same `dispatch`, such as a client that forwards the envelope to a
 * running server. The answer may be returned or promised.
 */
export interface ChapDispatcher {
  dispatch(envelope: Envelope): Envelope | Promise<Envelope>;
}

export interface ChapMcpOptions {
  /** Server name advertised to MCP clients. Default: "chap". */
  name?: string;
  /** Server version. Default: "0.3.4". */
  version?: string;
  /**
   * Restrict the CHAP methods this server offers. Default: all of them. A
   * tool the filter excludes is left out of the list and refused when called.
   */
  toolFilter?: (toolName: string) => boolean;
  /**
   * Make every call as this participant. `from` is filled in on each call,
   * and a call that names another `from` is refused. Use it where the server
   * is one participant's connection, such as an agent's, so the connection
   * cannot act in anyone else's name. The tools then list `from` as optional,
   * with this URI in its description. Default: each call names its own `from`.
   */
  participant?: string;
  /** Optional id generator for the envelopes emitted by tool calls. */
  envelopeIdFactory?: () => string | number;
}

/**
 * A tool's input schema as a server that acts as `participant` advertises
 * it. `from` is filled in, so a client need not send it, and the schema says
 * whose name the calls carry.
 */
function pinnedSchema(schema: JsonSchema, participant: string): JsonSchema {
  if (!schema.properties?.from) return schema;
  return {
    ...schema,
    properties: {
      ...schema.properties,
      from: {
        type: "string",
        description: `This server acts as ${participant}: it fills this in, and refuses a call that names anyone else.`,
      },
    },
    required: (schema.required ?? []).filter((key) => key !== "from"),
  };
}

/**
 * Wrap a CHAP Coordinator as an MCP server. The returned ``Server``
 * has ``tools/list`` and ``tools/call`` handlers registered for every
 * CHAP method; pass it to a transport (stdio, Streamable HTTP) to
 * start serving.
 */
export function makeChapMcpServer(coord: ChapDispatcher, options: ChapMcpOptions = {}): Server {
  const serverInfo = {
    name:    options.name    ?? "chap",
    version: options.version ?? "0.3.4",
  };

  const server = new Server(
    serverInfo,
    {
      capabilities: {
        tools: {},
      },
    },
  );

  let counter = 0;
  const nextId = options.envelopeIdFactory ?? (() => `mcp-${++counter}`);
  const filter = options.toolFilter ?? (() => true);

  const enabledTools: Tool[] = TOOL_NAMES
    .filter((name) => filter(name) && methodForTool(name) !== null)
    .map((name) => {
      const ann = TOOL_ANNOTATIONS[name];
      return {
        name,
        // The title was the tool name, which told a reader nothing they could
        // not already see. The annotation carries a readable one.
        title: ann?.title ?? name,
        description: TOOL_DESCRIPTIONS[name] ?? `CHAP method ${methodForTool(name)}.`,
        inputSchema: (options.participant === undefined
          ? SCHEMAS[name]
          : pinnedSchema(SCHEMAS[name], options.participant)) as Tool["inputSchema"],
        // Behavioural hints. Without these a client has only the prose to go
        // on when deciding whether a call is safe to make or to retry.
        ...(ann ? { annotations: ann } : {}),
      };
    });

  server.setRequestHandler(ListToolsRequestSchema, async (request) => {
    // Rejects a malformed or unsupported envelope before any work is done.
    const era = classifyEnvelope(request.params);
    if (era === "legacy") return { tools: enabledTools };

    // 2026-07-28 requires every result to be typed, and makes list results
    // cacheable. These fields are 2026 vocabulary, so they are emitted only
    // to a caller that asked in 2026 terms; a handshake-era session gets the
    // result shape its own revision defines.
    return {
      tools:      enabledTools,
      resultType: "complete",
      ttlMs:      LIST_TTL_MS,
      cacheScope: "public",
      _meta:      { [META_SERVER_INFO]: serverInfo },
    };
  });

  // server/discover (MCP 2026-07-28, SEP-2575). Servers MUST implement it;
  // clients MAY call it for up-front version selection, and on stdio a
  // client that supports both eras SHOULD probe with it first. Answering
  // the probe is what keeps this server usable by 2026-era clients: a
  // server that stays silent is only reachable through timeout-based
  // fallback to the legacy initialize handshake.
  //
  // Registered against a hand-written schema because the pinned SDK
  // (1.x) predates the 2026 era and does not export one yet. When the
  // SDK ships its own ServerDiscover types this can be swapped for them
  // without changing the wire behaviour.
  server.setRequestHandler(ServerDiscoverRequestSchema, async (request) => {
    // `server/discover` is 2026 vocabulary. A request that carries no
    // per-request envelope belongs to the handshake era, where the method
    // does not exist; answering it anyway would tell a dual-era client that
    // a legacy connection can speak 2026. Mirrors the Python SDK, which
    // returns MethodNotFound in the same situation.
    if (classifyEnvelope(request.params) === "legacy") {
      // `data` carries the method name, matching what the Python SDK emits,
      // so a client sees the same error either side.
      throw new ProtocolError(ErrorCode.MethodNotFound, "Method not found", "server/discover");
    }
    return {
    resultType:       "complete",
    supportedVersions: SUPPORTED_PROTOCOL_VERSIONS,
    capabilities:     { tools: {} },
    instructions:
      "CHAP Coordinator exposed as MCP tools. Every accepted call that " +
      "changes state is recorded on the CHAP audit log, as is a member's " +
      "refused attempt at a governed action. Governed methods enforce " +
      "membership and review rules server-side.",
    ttlMs:      LIST_TTL_MS,
    cacheScope: "public",
    _meta:      { [META_SERVER_INFO]: serverInfo },
    };
  });

  server.setRequestHandler(CallToolRequestSchema, async (request): Promise<CallToolResult> => {
    const era = classifyEnvelope(request.params);
    // `resultType` is 2026 vocabulary: emitted to a modern caller, withheld
    // from a handshake-era one whose revision does not define the field.
    const terminal = <T extends object>(result: T): T =>
      (era === "modern" ? { resultType: "complete", ...result } : result) as T;

    const toolName = request.params.name;
    const rawArgs = (request.params.arguments ?? {}) as Record<string, unknown>;
    const method = methodForTool(toolName);

    if (!method) {
      return terminal({
        isError: true,
        content: [{ type: "text", text: `Unknown CHAP tool: ${toolName}` }],
      });
    }
    // The filter decides what this server offers, so a tool it excludes is
    // refused here as well as left out of the list. Hidden alone, it still ran
    // for any client that called it by name.
    if (!filter(toolName)) {
      return terminal({
        isError: true,
        content: [{ type: "text", text: `CHAP tool not offered by this server: ${toolName}` }],
      });
    }

    // Normalise stringified-JSON arguments (a common MCP-client
    // behaviour) before they reach the protocol core. See coerceToolArgs.
    const args = { ...coerceToolArgs(toolName, rawArgs) };
    if (options.participant !== undefined) {
      if (args.from !== undefined && args.from !== options.participant) {
        return terminal({
          isError: true,
          content: [{
            type: "text",
            text: `This server acts as ${options.participant}; a call cannot name another from (${JSON.stringify(args.from)}).`,
          }],
        });
      }
      args.from = options.participant;
    }

    const envelope: Envelope = {
      jsonrpc: "2.0",
      id: nextId(),
      method,
      params: args,
    };

    let response: Envelope;
    try {
      response = await coord.dispatch(envelope);
    } catch (e) {
      // A forwarding dispatcher's own failures are written for the client:
      // the server could not be reached, or did not answer in JSON-RPC.
      if (e instanceof ChapEndpointError) {
        return terminal({ isError: true, content: [{ type: "text", text: e.message }] });
      }
      // Anything else is a fault in the server. Its message can carry a path
      // or a stack line, so it goes to the server's log, which the client never sees.
      console.error("CHAP dispatch failed:", e);
      return terminal({
        isError: true,
        content: [{ type: "text", text: FAILED_INSIDE.message }],
      });
    }

    if (response.error) {
      // -32603 is a fault inside the coordinator, and its data can carry the
      // exception's text. It goes to the server's log, and the client is
      // told only that the call failed. A refusal is passed on as it came.
      const internal = response.error.code === INTERNAL_ERROR;
      if (internal) console.error("CHAP call failed:", response.error);
      return terminal({
        isError: true,
        content: [{
          type: "text",
          text: JSON.stringify(internal ? FAILED_INSIDE : {
            chap_error: response.error.code,
            message:    response.error.message,
            ...(response.error.data !== undefined ? { data: response.error.data } : {}),
          }, null, 2),
        }],
      });
    }

    return terminal({
      content: [{
        type: "text",
        text: JSON.stringify(response.result, null, 2),
      }],
    });
  });

  return server;
}

/**
 * Lower-level helper: translate a tool call to a CHAP envelope and
 * back. Useful for tests and for embedding the adapter inside a
 * larger MCP server that registers its own additional tools.
 */
export function dispatchToolCall(
  coord: Coordinator,
  toolName: string,
  args: Record<string, unknown>,
  envelopeId: string | number = "mcp-call",
): Envelope {
  const method = methodForTool(toolName);
  if (!method) {
    return {
      jsonrpc: "2.0",
      id: envelopeId,
      error: { code: -32601, message: `Unknown CHAP tool: ${toolName}` },
    };
  }
  return coord.dispatch({
    jsonrpc: "2.0",
    id: envelopeId,
    method,
    params: coerceToolArgs(toolName, args ?? {}),
  });
}

export interface ChapMcpHttpOptions extends ChapMcpOptions {
  /**
   * The largest request body read, when the caller has not read it. Default
   * 1,114,112 bytes: the coordinator's default envelope limit of 1 MiB, and
   * 64 KiB for the MCP request around it.
   */
  maxBodyBytes?: number;
}

/** Answers one HTTP request on the MCP endpoint. Pass `parsedBody` when the server has read and parsed the body already. */
export type ChapMcpHttpHandler = (req: IncomingMessage, res: ServerResponse, parsedBody?: unknown) => Promise<void>;

/**
 * Serve MCP over HTTP: a handler for a Node `http` request and response,
 * for an endpoint such as `/mcp`, with the version handling of the stdio
 * server. A request with no MCP-Protocol-Version header, or a handshake-era
 * one, is answered by the SDK's Streamable HTTP transport, statelessly; a
 * 2026-07-28 request is answered as that revision's single exchange, one
 * JSON-RPC request and one JSON answer. `options` are those of
 * makeChapMcpServer, so `participant` and `toolFilter` hold over HTTP as on
 * stdio.
 *
 * It checks neither Host nor Origin: mount it behind the checks the server
 * applies to every request.
 *
 *   const mcp = chapMcpHttpHandler(coord, { participant: "agent:assistant", toolFilter: (n) => AGENT_MCP_TOOLS.has(n) });
 *   createServer((req, res) => (req.url === "/mcp" ? mcp(req, res) : notFound(res)));
 */
export function chapMcpHttpHandler(coord: ChapDispatcher, options: ChapMcpHttpOptions = {}): ChapMcpHttpHandler {
  const { maxBodyBytes = DEFAULT_MAX_BODY_BYTES, ...serverOptions } = options;
  return mcpHttpHandler(() => makeChapMcpServer(coord, serverOptions), maxBodyBytes);
}
