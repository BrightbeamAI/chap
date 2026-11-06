/**
 * @brightbeamai/chap-coordinator-a2a/card
 *
 * Build the AgentCard advertised at the agent's well-known URL.
 * Every CHAP method becomes a discrete ``AgentSkill`` with id
 * ``chap.<method>``, matching the MCP transport's tool naming so a
 * caller fluent in one is fluent in the other.
 */

import type { AgentCard, AgentInterface, AgentSkill } from "@a2a-js/sdk";

import { TOOL_NAMES, methodForTool } from "@brightbeamai/chap-coordinator-mcp/schemas";
import { TOOL_DESCRIPTIONS } from "@brightbeamai/chap-coordinator-mcp/tools";

/**
 * The media type of every part a CHAP skill takes and returns: the call's
 * params in, and its result or refusal out, as JSON data parts. A2A names
 * input and output modes by media type.
 */
export const CHAP_MEDIA_TYPE = "application/json";

export interface ChapAgentCardOptions {
  /** The URL of the JSON-RPC endpoint, advertised on the Agent Card. */
  baseUrl: string;
  name?: string;
  description?: string;
  version?: string;
  /** Restrict which CHAP methods are exposed as skills. Default: all 39. */
  skillFilter?: (skillId: string) => boolean;
}

const DEFAULT_DESCRIPTION =
  "Collaborative Human-Agent Protocol coordinator. " +
  "Exposes every CHAP method as a discrete skill so remote agents " +
  "can discover and drive workspaces, tasks, reviews, deliberations, " +
  "handoffs, and audit operations.";

/**
 * The card lists the JSON-RPC endpoint twice: at A2A 1.0, and at 0.3 for a
 * client that has not moved. A v0.3 client reaches the endpoint only where
 * the card lists it at 0.3, and only where the server's handlers have
 * `legacyCompat` enabled, as the reference server's do.
 */
export function makeChapAgentCard(options: ChapAgentCardOptions): AgentCard {
  if (!options.baseUrl) {
    throw new Error("baseUrl is required");
  }
  const filter = options.skillFilter ?? (() => true);

  const skills: AgentSkill[] = TOOL_NAMES
    .filter((name) => filter(name) && methodForTool(name) !== null)
    .map<AgentSkill>((name) => {
      const method = methodForTool(name)!;
      return {
        id: name,
        name,
        description: TOOL_DESCRIPTIONS[name] ?? `CHAP method ${method}.`,
        tags: ["chap", method.split(".")[0]],
        examples: [],
        inputModes:  [CHAP_MEDIA_TYPE],
        outputModes: [CHAP_MEDIA_TYPE],
        securityRequirements: [],
      };
    });

  const jsonRpc = (protocolVersion: string): AgentInterface =>
    ({ url: options.baseUrl, protocolBinding: "JSONRPC", tenant: "", protocolVersion });

  return {
    name:        options.name        ?? "CHAP Coordinator",
    description: options.description ?? DEFAULT_DESCRIPTION,
    supportedInterfaces: [jsonRpc("1.0"), jsonRpc("0.3")],
    provider:    undefined,
    version:     options.version     ?? "0.3.4",
    capabilities: {
      streaming: false,
      pushNotifications: false,
      extensions: [],
      extendedAgentCard: false,
    },
    securitySchemes: {},
    securityRequirements: [],
    defaultInputModes:  [CHAP_MEDIA_TYPE],
    defaultOutputModes: [CHAP_MEDIA_TYPE],
    skills,
    signatures: [],
  };
}
