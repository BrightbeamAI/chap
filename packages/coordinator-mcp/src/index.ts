/**
 * @brightbeamai/chap-coordinator-mcp
 *
 * MCP server adapter for a CHAP Coordinator. Wraps a Coordinator
 * instance and exposes every CHAP method as an MCP tool.
 *
 * Spec target: MCP 2026-07-28 (current), serving MCP 2025-11-25
 * clients as well. CHAP 0.2.
 *
 * Usage (stdio):
 *
 *   import { Coordinator } from "@brightbeamai/chap-coordinator";
 *   import { makeChapMcpServer } from "@brightbeamai/chap-coordinator-mcp";
 *   import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
 *
 *   const coord = new Coordinator({ ... });
 *   const server = makeChapMcpServer(coord, { name: "chap", version: "0.2.10" });
 *   await server.connect(new StdioServerTransport());
 *
 * Usage (Streamable HTTP): see reference/mcp-server-ts/server.ts.
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

import { SCHEMAS, TOOL_NAMES, methodForTool, coerceToolArgs } from "./schemas.js";
import { TOOL_DESCRIPTIONS } from "./tools.js";
import { classifyEnvelope, ProtocolError, SUPPORTED_PROTOCOL_VERSIONS, type ProtocolEra } from "./envelope.js";

export { SCHEMAS, TOOL_NAMES, schemaFor, methodForTool, coerceToolArgs } from "./schemas.js";
export { TOOL_DESCRIPTIONS } from "./tools.js";
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
  params: z.optional(z.object({ _meta: z.optional(z.record(z.string(), z.unknown())) }).loose()),
});

export interface ChapMcpOptions {
  /** Server name advertised to MCP clients. Default: "chap". */
  name?: string;
  /** Server version. Default: "0.2.10". */
  version?: string;
  /** Override the list of CHAP methods to expose. Default: all 39. */
  toolFilter?: (toolName: string) => boolean;
  /** Optional id generator for the envelopes emitted by tool calls. */
  envelopeIdFactory?: () => string | number;
}

/**
 * Wrap a CHAP Coordinator as an MCP server. The returned ``Server``
 * has ``tools/list`` and ``tools/call`` handlers registered for every
 * CHAP method; pass it to a transport (stdio, Streamable HTTP) to
 * start serving.
 */
export function makeChapMcpServer(coord: Coordinator, options: ChapMcpOptions = {}): Server {
  const serverInfo = {
    name:    options.name    ?? "chap",
    version: options.version ?? "0.2.10",
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
    .map((name) => ({
      name,
      title: name,
      description: TOOL_DESCRIPTIONS[name] ?? `CHAP method ${methodForTool(name)}.`,
      inputSchema: SCHEMAS[name] as Tool["inputSchema"],
    }));

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
      "CHAP Coordinator exposed as MCP tools. Every tool call is recorded " +
      "on the CHAP audit log; governed methods enforce membership and " +
      "review rules server-side.",
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

    // Normalise stringified-JSON arguments (a common MCP-client
    // behaviour) before they reach the protocol core. See coerceToolArgs.
    const args = coerceToolArgs(toolName, rawArgs);

    const envelope: Envelope = {
      jsonrpc: "2.0",
      id: nextId(),
      method,
      params: args,
    };

    let response: Envelope;
    try {
      response = coord.dispatch(envelope);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return terminal({
        isError: true,
        content: [{ type: "text", text: `CHAP dispatch threw: ${msg}` }],
      });
    }

    if (response.error) {
      return terminal({
        isError: true,
        content: [{
          type: "text",
          text: JSON.stringify({
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
