/**
 * @brightbeamai/chap-coordinator-a2a/executor
 *
 * Implementation of the @a2a-js/sdk ``AgentExecutor`` interface that
 * dispatches incoming messages through a CHAP Coordinator.
 *
 * Skill ids on inbound messages must take the form ``chap.<method>``;
 * the executor extracts the skill id from message metadata or the
 * data part and translates the call into a CHAP envelope.
 */

import { randomUUID } from "node:crypto";

import { AgentEvent, type AgentExecutor, type ExecutionEventBus, type RequestContext } from "@a2a-js/sdk/server";
import { Role, type Message, type Part } from "@a2a-js/sdk";

import type { Coordinator, Envelope } from "@brightbeamai/chap-coordinator";
import { methodForTool } from "@brightbeamai/chap-coordinator-mcp/schemas";

import { CHAP_MEDIA_TYPE } from "./card.js";

export interface ChapAgentExecutorOptions {
  /** Optional id generator for CHAP envelope ids. */
  envelopeIdFactory?: () => string;
  /**
   * Restrict the CHAP skills this executor runs. A skill the filter excludes
   * is refused with -32601. Pass the same filter as the Agent Card's
   * `skillFilter`: the card only lists skills, and the executor is what runs
   * them. Default: all 39.
   */
  skillFilter?: (skillId: string) => boolean;
}

export class ChapAgentExecutor implements AgentExecutor {
  private readonly nextEnvelopeId: () => string;
  private readonly skillFilter: (skillId: string) => boolean;

  constructor(
    private readonly coord: Coordinator,
    options: ChapAgentExecutorOptions = {},
  ) {
    let counter = 0;
    this.nextEnvelopeId = options.envelopeIdFactory ?? (() => `a2a-${++counter}`);
    this.skillFilter = options.skillFilter ?? (() => true);
  }

  async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    const contextId = requestContext.contextId || requestContext.userMessage.contextId || randomUUID();

    let response: Envelope;
    try {
      response = dispatchA2aMessage(this.coord, requestContext.userMessage, {
        envelopeId: this.nextEnvelopeId(),
        skillFilter: this.skillFilter,
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      response = { jsonrpc: "2.0", error: { code: -32603, message: `CHAP dispatch threw: ${msg}` } };
    }

    if (response.error) {
      const body: Record<string, unknown> = { chap_error: response.error.code, message: response.error.message };
      if (response.error.data !== undefined) body.data = response.error.data;
      eventBus.publish(AgentEvent.message(this.agentMessage(contextId, body, { is_error: true })));
    } else {
      eventBus.publish(AgentEvent.message(this.agentMessage(contextId, coerceToRecord(response.result))));
    }
    eventBus.finished();
  }

  /**
   * A CHAP call takes effect within execute, before execute returns, so no
   * call is ever left running to stop. The request leaves the task as it is,
   * and the SDK answers it with TaskNotCancelableError.
   */
  async cancelTask(_taskId: string, eventBus: ExecutionEventBus): Promise<void> {
    eventBus.finished();
  }

  /** The agent's reply: one JSON data part. No task is made for it, so it names none. */
  private agentMessage(contextId: string, data: Record<string, unknown>, metadata?: Record<string, unknown>): Message {
    return {
      messageId: `chap-resp-${this.nextEnvelopeId()}`,
      contextId,
      taskId: "",
      role: Role.ROLE_AGENT,
      parts: [dataPart(data, metadata)],
      metadata: undefined,
      extensions: [],
      referenceTaskIds: [],
    };
  }
}

/** Convenience constructor mirroring ``make_chap_agent_executor``. */
export function makeChapAgentExecutor(
  coord: Coordinator,
  options?: ChapAgentExecutorOptions,
): ChapAgentExecutor {
  return new ChapAgentExecutor(coord, options);
}

// ============================================================
//   Direct-dispatch helper (testing + embedded use)
// ============================================================

export interface DispatchA2aMessageOptions {
  /** The id of the CHAP envelope. Default: "a2a-call". */
  envelopeId?: string | number;
  /** Refuse a skill the filter excludes, with -32601. */
  skillFilter?: (skillId: string) => boolean;
}

/**
 * Translate an A2A Message into a CHAP envelope and dispatch it
 * directly. Useful for tests and for embedding the adapter inside a
 * larger A2A server.
 */
export function dispatchA2aMessage(
  coord: Coordinator,
  message: Message,
  options: DispatchA2aMessageOptions = {},
): Envelope {
  const id = options.envelopeId ?? "a2a-call";
  const { skillId, params } = extractSkillAndParams(message);
  const method = skillId ? methodForTool(skillId) : null;

  if (!method) {
    return {
      jsonrpc: "2.0",
      id,
      error: {
        code: -32601,
        message: `Unknown CHAP skill: ${skillId ?? "(missing)"}. ` +
                 `Expected an A2A skill id of the form 'chap.<method>'.`,
      },
    };
  }
  if (options.skillFilter && !options.skillFilter(skillId!)) {
    return {
      jsonrpc: "2.0",
      id,
      error: { code: -32601, message: `CHAP skill not offered by this server: ${skillId}` },
    };
  }

  return coord.dispatch({
    jsonrpc: "2.0",
    id,
    method,
    params: params ?? {},
  });
}

// ============================================================
//   Internals
// ============================================================

/** A part carrying JSON, with the media type the card names for every skill. */
function dataPart(data: unknown, metadata?: Record<string, unknown>): Part {
  return { content: { $case: "data", value: data }, metadata, filename: "", mediaType: CHAP_MEDIA_TYPE };
}

function coerceToRecord(value: unknown): Record<string, unknown> {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return { result: value };
}

/**
 * Pull the CHAP skill id and params out of an A2A Message. Looks at
 * the message metadata first (where the A2A client typically puts a
 * skill hint), then falls back to the first data part's value.
 */
function extractSkillAndParams(
  message: Message,
): { skillId: string | null; params: Record<string, unknown> | null } {
  let skillId: string | null = null;
  let params: Record<string, unknown> | null = null;

  const md = (message.metadata ?? {}) as Record<string, unknown>;
  if (typeof md.skill === "string") skillId = md.skill;

  for (const part of message.parts ?? []) {
    if (part.content?.$case !== "data") continue;
    const blob = part.content.value;
    if (blob === null || typeof blob !== "object" || Array.isArray(blob)) break;
    if (skillId === null && typeof blob.skill === "string") {
      skillId = blob.skill;
    }
    const blobParams = blob.params;
    if (blobParams && typeof blobParams === "object" && !Array.isArray(blobParams)) {
      params = blobParams as Record<string, unknown>;
    } else {
      // Treat the whole data blob as the params, minus the skill key.
      const { skill: _skill, ...rest } = blob as Record<string, unknown>;
      params = rest;
    }
    break;
  }

  return { skillId, params };
}
