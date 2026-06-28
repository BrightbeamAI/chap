/**
 * @chap/coordinator-a2a/card
 *
 * Build the AgentCard advertised at the agent's well-known URL.
 * Every CHAP method becomes a discrete ``AgentSkill`` with id
 * ``chap.<method>``, matching the MCP transport's tool naming so a
 * caller fluent in one is fluent in the other.
 */

import type { AgentCard, AgentSkill } from "@a2a-js/sdk";

import { TOOL_NAMES, methodForTool } from "@chap/coordinator-mcp/schemas";
import { TOOL_DESCRIPTIONS } from "@chap/coordinator-mcp/tools";

export interface ChapAgentCardOptions {
  /** Base URL advertised on the Agent Card. */
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
        inputModes:  ["data"],
        outputModes: ["data"],
      };
    });

  return {
    name:        options.name        ?? "CHAP Coordinator",
    description: options.description ?? DEFAULT_DESCRIPTION,
    url:         options.baseUrl,
    version:     options.version     ?? "0.2.6",
    protocolVersion: "0.3.0",
    preferredTransport: "JSONRPC",
    defaultInputModes:  ["data"],
    defaultOutputModes: ["data"],
    capabilities: {
      streaming: false,
      pushNotifications: false,
      stateTransitionHistory: false,
    },
    skills,
  };
}
