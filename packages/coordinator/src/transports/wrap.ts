/**
 * @brightbeamai/chap-coordinator/transports/wrap
 *
 * Inward integration helpers: turn an external tool/agent event into
 * a CHAP envelope landing in a workspace's audit log. Library
 * counterpart to the patterns described in
 * ``integrations/CHAP-with-MCP.md`` and ``integrations/CHAP-with-A2A.md``.
 *
 * The outward transports let external clients drive a CHAP workspace.
 * These helpers go the other direction: when work has already
 * happened outside CHAP, wrap it so the audit log carries a faithful,
 * citable record.
 *
 * Spec target: CHAP 0.2. MCP 2025-11-25. A2A 1.0.
 */

import { contentHash } from "../canonical.js";
import type { Coordinator } from "../coordinator.js";

export { contentHash } from "../canonical.js";

export interface WrapMcpToolCallOptions {
  /** Participant URI making the call. Must already be a workspace member. */
  caller: string;
  /** MCP tool name, e.g. ``"github.create_issue"``. */
  tool: string;
  /** Arguments passed to the MCP tool. */
  args: Record<string, unknown>;
  /** Return value from the MCP tool. */
  result: unknown;
  /** Optional MCP server identifier, e.g. "github". */
  server?: string;
  /** Override the default task kind ``"mcp_call:<tool>"``. */
  taskKind?: string;
  /** Optional routing hints attached to the CHAP task. */
  routingHints?: Record<string, unknown>;
  /** Optional confidence value attached to task.complete. */
  confidence?: number | string;
}

export interface WrapResult {
  task_id:     string;
  input_hash:  string;
  output_hash: string;
}

/**
 * Wrap a completed MCP tool call as a CHAP task pair.
 *
 * Emits ``task.create`` + ``task.complete`` envelopes into the
 * workspace so the MCP call shows up as a first-class audit entry.
 * The result artefact is the MCP tool's return value, plus a
 * ``citations[]`` entry with the input/output hashes per
 * ``integrations/CHAP-with-MCP.md`` §2.
 */
export function wrapMcpToolCall(
  coord: Coordinator,
  workspace: string,
  options: WrapMcpToolCallOptions,
): WrapResult {
  if (!workspace) throw new Error("workspace is required");
  if (!options.caller) throw new Error("caller is required");
  if (!options.tool) throw new Error("tool is required");

  const inputHash = contentHash(options.args);
  const outputHash = contentHash(options.result);
  const kind = options.taskKind ?? `mcp_call:${options.tool}`;

  const citations = [{
    kind:         "mcp_tool_call" as const,
    server:       options.server ?? "unknown",
    tool:         options.tool,
    input_hash:   inputHash,
    output_hash:  outputHash,
  }];

  const createParams: Record<string, unknown> = {
    workspace,
    from:     options.caller,
    kind,
    assignee: options.caller,
    input:    options.args,
  };
  if (options.routingHints) createParams.routing_hints = { ...options.routingHints };

  const createResp = coord.dispatch({
    jsonrpc: "2.0",
    id:      `wrap-${options.tool}-create`,
    method:  "task.create",
    params:  createParams,
  });
  if (createResp.error) throw new Error(`task.create failed: ${JSON.stringify(createResp.error)}`);
  const taskId = (createResp.result as { task_id: string }).task_id;

  coord.dispatch({
    jsonrpc: "2.0",
    id:      `wrap-${options.tool}-update`,
    method:  "task.update",
    params:  { workspace, from: options.caller, task_id: taskId, state: "in_progress" },
  });

  const completeParams: Record<string, unknown> = {
    workspace,
    from:     options.caller,
    task_id:  taskId,
    output:   { result: options.result, citations },
  };
  if (options.confidence !== undefined) completeParams.confidence = options.confidence;

  const completeResp = coord.dispatch({
    jsonrpc: "2.0",
    id:      `wrap-${options.tool}-complete`,
    method:  "task.complete",
    params:  completeParams,
  });
  if (completeResp.error) throw new Error(`task.complete failed: ${JSON.stringify(completeResp.error)}`);

  return { task_id: taskId, input_hash: inputHash, output_hash: outputHash };
}

// ============================================================

export interface WrapA2aExchangeOptions {
  /** URI of the local bridge participant. Must be a workspace member. */
  bridgeUri: string;
  /** Identifier of the remote A2A agent. Recorded in the citation. */
  remoteAgent: string;
  /** The A2A message body sent outbound. */
  sent: Record<string, unknown>;
  /** The A2A message body received in response. */
  received: Record<string, unknown>;
  /** CHAP task kind. Default ``"a2a_exchange"``. */
  taskKind?: string;
  /** Optional routing hints attached to the task. */
  routingHints?: Record<string, unknown>;
  /** Optional confidence value attached to task.complete. */
  confidence?: number | string;
}

export interface WrapA2aResult {
  task_id:       string;
  sent_hash:     string;
  received_hash: string;
}

/**
 * Wrap a completed A2A message exchange as a CHAP task pair.
 *
 * Implements the bridge-participant pattern from
 * ``integrations/CHAP-with-A2A.md`` §3 as a library helper.
 */
export function wrapA2aMessageExchange(
  coord: Coordinator,
  workspace: string,
  options: WrapA2aExchangeOptions,
): WrapA2aResult {
  if (!workspace) throw new Error("workspace is required");
  if (!options.bridgeUri) throw new Error("bridgeUri is required");
  if (!options.remoteAgent) throw new Error("remoteAgent is required");

  const sentHash = contentHash(options.sent);
  const receivedHash = contentHash(options.received);
  const kind = options.taskKind ?? "a2a_exchange";

  const citation = {
    kind:           "a2a_exchange" as const,
    remote_agent:   options.remoteAgent,
    sent_hash:      sentHash,
    received_hash:  receivedHash,
  };

  const createParams: Record<string, unknown> = {
    workspace,
    from:     options.bridgeUri,
    kind,
    assignee: options.bridgeUri,
    input:    { remote_agent: options.remoteAgent, sent: options.sent },
  };
  if (options.routingHints) createParams.routing_hints = { ...options.routingHints };

  const createResp = coord.dispatch({
    jsonrpc: "2.0", id: "wrap-a2a-create",
    method: "task.create", params: createParams,
  });
  if (createResp.error) throw new Error(`task.create failed: ${JSON.stringify(createResp.error)}`);
  const taskId = (createResp.result as { task_id: string }).task_id;

  coord.dispatch({
    jsonrpc: "2.0", id: "wrap-a2a-update",
    method: "task.update",
    params: { workspace, from: options.bridgeUri,
              task_id: taskId, state: "in_progress" },
  });

  const completeParams: Record<string, unknown> = {
    workspace, from: options.bridgeUri, task_id: taskId,
    output: { received: options.received, citations: [citation] },
  };
  if (options.confidence !== undefined) completeParams.confidence = options.confidence;

  const completeResp = coord.dispatch({
    jsonrpc: "2.0", id: "wrap-a2a-complete",
    method: "task.complete", params: completeParams,
  });
  if (completeResp.error) throw new Error(`task.complete failed: ${JSON.stringify(completeResp.error)}`);

  return { task_id: taskId, sent_hash: sentHash, received_hash: receivedHash };
}
