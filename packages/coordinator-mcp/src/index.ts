/**
 * @chap/coordinator-mcp
 *
 * MCP server adapter for a CHAP Coordinator. Wraps a Coordinator
 * instance and exposes every CHAP method as an MCP tool.
 *
 * Spec target: MCP 2025-11-25 (current stable). CHAP 0.2.
 *
 * Usage (stdio):
 *
 *   import { Coordinator } from "@chap/coordinator";
 *   import { makeChapMcpServer } from "@chap/coordinator-mcp";
 *   import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
 *
 *   const coord = new Coordinator({ ... });
 *   const server = makeChapMcpServer(coord, { name: "chap", version: "0.2.6" });
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
  type CallToolResult,
  type Tool,
} from "@modelcontextprotocol/sdk/types.js";
import type { Coordinator, Envelope } from "@chap/coordinator";

import { SCHEMAS, TOOL_NAMES, methodForTool, coerceToolArgs } from "./schemas.js";
import { TOOL_DESCRIPTIONS } from "./tools.js";

export { SCHEMAS, TOOL_NAMES, schemaFor, methodForTool, coerceToolArgs } from "./schemas.js";
export { TOOL_DESCRIPTIONS } from "./tools.js";
export type { JsonSchema } from "./schemas.js";

export interface ChapMcpOptions {
  /** Server name advertised to MCP clients. Default: "chap". */
  name?: string;
  /** Server version. Default: "0.2.6". */
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
  const server = new Server(
    {
      name:    options.name    ?? "chap",
      version: options.version ?? "0.2.6",
    },
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

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: enabledTools,
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request): Promise<CallToolResult> => {
    const toolName = request.params.name;
    const rawArgs = (request.params.arguments ?? {}) as Record<string, unknown>;
    const method = methodForTool(toolName);

    if (!method) {
      return {
        isError: true,
        content: [{ type: "text", text: `Unknown CHAP tool: ${toolName}` }],
      };
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
      return {
        isError: true,
        content: [{ type: "text", text: `CHAP dispatch threw: ${msg}` }],
      };
    }

    if (response.error) {
      return {
        isError: true,
        content: [{
          type: "text",
          text: JSON.stringify({
            chap_error: response.error.code,
            message:    response.error.message,
            ...(response.error.data !== undefined ? { data: response.error.data } : {}),
          }, null, 2),
        }],
      };
    }

    return {
      content: [{
        type: "text",
        text: JSON.stringify(response.result, null, 2),
      }],
    };
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
