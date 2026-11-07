/**
 * @brightbeamai/chap-coordinator-a2a/card
 *
 * Build the AgentCard advertised at the agent's well-known URL.
 * Each CHAP method the server offers is a discrete ``AgentSkill`` with
 * id ``chap.<method>``, matching the MCP transport's tool naming so a
 * caller fluent in one is fluent in the other.
 */

import type { AgentCard, AgentInterface, AgentSkill } from "@a2a-js/sdk";

import { TOOL_NAMES, methodForTool } from "@brightbeamai/chap-coordinator-mcp/schemas";
import { AGENT_MCP_TOOLS, TOOL_DESCRIPTIONS } from "@brightbeamai/chap-coordinator-mcp/tools";

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
  /**
   * Which CHAP methods are listed as skills. Default: the agent's, the
   * AGENT_MCP_TOOLS set; `() => true` lists every method. Pass the same
   * filter as the executor's.
   */
  skillFilter?: (skillId: string) => boolean;
  /**
   * Whether the card also lists the endpoint at A2A 0.3, which the server
   * answers where its handlers have `legacyCompat` enabled. Default true;
   * pass false for a server whose handlers do not, so the card claims only
   * what the server answers.
   */
  legacyCompat?: boolean;
}

const DEFAULT_DESCRIPTION =
  "Collaborative Human-Agent Protocol coordinator. " +
  "Each CHAP method it offers is a discrete skill, so remote agents " +
  "can discover and drive the work in its workspaces: tasks, reviews " +
  "and the audit log.";

/**
 * The card lists the JSON-RPC endpoint at A2A 1.0, and with `legacyCompat`,
 * the default, at 0.3 as well, for a client that has not moved. A v0.3
 * client reaches the endpoint only where the card lists it at 0.3, and only
 * where the server's handlers have `legacyCompat` enabled, as the reference
 * server's do.
 */
export function makeChapAgentCard(options: ChapAgentCardOptions): AgentCard {
  if (!options.baseUrl) {
    throw new Error("baseUrl is required");
  }
  const filter = options.skillFilter ?? ((skillId: string) => AGENT_MCP_TOOLS.has(skillId));

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
    supportedInterfaces: options.legacyCompat === false ? [jsonRpc("1.0")] : [jsonRpc("1.0"), jsonRpc("0.3")],
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
