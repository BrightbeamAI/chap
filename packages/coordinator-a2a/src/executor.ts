/**
 * @brightbeamai/chap-coordinator-a2a/executor
 *
 * Implementation of the @a2a-js/sdk ``AgentExecutor`` interface that
 * dispatches incoming messages through a CHAP Coordinator.
 *
 * Skill ids on inbound messages must take the form ``chap.<method>``;
 * the executor extracts the skill id from message metadata or the
 * data part and translates the call into a CHAP envelope.
 *
 * An answer is an agent message carrying the result. A refusal is a task
 * that has failed, in A2A's terms: rejected when the coordinator refused
 * the call, failed when it could not complete it. Either way the status
 * message carries the CHAP error, `{chap_error, message, data?}`, so a
 * client that reads only A2A states sees the failure, and one that reads
 * CHAP codes has them.
 */

import { randomUUID } from "node:crypto";

import { AgentEvent, type AgentExecutor, type ExecutionEventBus, type RequestContext } from "@a2a-js/sdk/server";
import { Role, TaskState, type Message, type Part } from "@a2a-js/sdk";

import type { Coordinator, Envelope } from "@brightbeamai/chap-coordinator";
import { methodForTool } from "@brightbeamai/chap-coordinator-mcp/schemas";
import { AGENT_MCP_TOOLS } from "@brightbeamai/chap-coordinator-mcp/tools";

import { CHAP_MEDIA_TYPE } from "./card.js";

/** JSON-RPC's internal error: a fault inside the coordinator, which refused nothing. */
const INTERNAL_ERROR = -32603;

/** What a client is told of a fault inside the server; the detail is in the server's log. */
const FAILED_INSIDE = "The CHAP call failed inside the server; its log has the detail.";

export interface ChapAgentExecutorOptions {
  /** Optional id generator for CHAP envelope ids. */
  envelopeIdFactory?: () => string;
  /**
   * The CHAP skills this executor runs; a skill the filter excludes is
   * refused with -32601. Default: the agent's, the AGENT_MCP_TOOLS set, so
   * a connection made for an agent cannot pause the workspace, change its
   * members or decide who reviews; `() => true` runs every method. Pass the
   * same filter as the Agent Card's `skillFilter`: the card only lists
   * skills, and the executor is what runs them.
   */
  skillFilter?: (skillId: string) => boolean;
  /**
   * Make every call as this participant. `from` is filled in on each call,
   * and a call that names another `from` is refused with -32011. Use it
   * where the server is one participant's connection, such as an
   * orchestrator's, so the connection cannot act in anyone else's name, as
   * `participant` does for the MCP server. A `human:` URI is refused: a
   * person decides in a desk. Default: each call names its own `from`.
   */
  participant?: string;
}

export class ChapAgentExecutor implements AgentExecutor {
  private readonly nextEnvelopeId: () => string;
  private readonly skillFilter: (skillId: string) => boolean;
  private readonly participant: string | undefined;

  constructor(
    private readonly coord: Coordinator,
    options: ChapAgentExecutorOptions = {},
  ) {
    refusePerson(options.participant);
    let counter = 0;
    this.nextEnvelopeId = options.envelopeIdFactory ?? (() => `a2a-${++counter}`);
    this.skillFilter = options.skillFilter ?? ((skillId) => AGENT_MCP_TOOLS.has(skillId));
    this.participant = options.participant;
  }

  async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    const contextId = requestContext.contextId || requestContext.userMessage.contextId || randomUUID();

    let error: Envelope["error"];
    let result: unknown;
    try {
      const response = dispatchA2aMessage(this.coord, requestContext.userMessage, {
        envelopeId: this.nextEnvelopeId(),
        skillFilter: this.skillFilter,
        ...(this.participant !== undefined ? { participant: this.participant } : {}),
      });
      if (response.error?.code === INTERNAL_ERROR) {
        // A fault inside the coordinator, whose data can carry the
        // exception's text. It goes to the server's log, and the client is
        // told only that the call failed. A refusal is passed on as it came.
        console.error("CHAP call failed:", response.error);
        error = { code: INTERNAL_ERROR, message: FAILED_INSIDE };
      } else {
        error = response.error;
        result = response.result;
      }
    } catch (e) {
      // A fault in the server. Its message can carry a path or a stack line,
      // so it goes to the server's log, which the client never sees.
      console.error("CHAP dispatch failed:", e);
      error = { code: INTERNAL_ERROR, message: FAILED_INSIDE };
    }

    if (error) {
      const { code, message, data } = error;
      const taskId = requestContext.taskId;
      eventBus.publish(AgentEvent.task({
        id: taskId,
        contextId,
        status: {
          state: code === INTERNAL_ERROR ? TaskState.TASK_STATE_FAILED : TaskState.TASK_STATE_REJECTED,
          message: this.agentMessage(contextId, taskId, { chap_error: code, message, ...(data !== undefined ? { data } : {}) }),
          timestamp: new Date().toISOString(),
        },
        artifacts: [],
        history: [requestContext.userMessage],
        metadata: undefined,
      }));
    } else {
      eventBus.publish(AgentEvent.message(this.agentMessage(contextId, "", coerceToRecord(result))));
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

  /**
   * The agent's message: one JSON data part. An answer is a message alone,
   * with no task made for it, so it names none; a refusal names its task.
   */
  private agentMessage(contextId: string, taskId: string, data: Record<string, unknown>): Message {
    return {
      messageId: `chap-resp-${this.nextEnvelopeId()}`,
      contextId,
      taskId,
      role: Role.ROLE_AGENT,
      parts: [dataPart(data)],
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
  /** Refuse a skill the filter excludes, with -32601. Default: every skill runs. */
  skillFilter?: (skillId: string) => boolean;
  /**
   * Make the call as this participant, and refuse one that names another
   * `from`, with -32011. A `human:` URI is refused.
   */
  participant?: string;
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
  refusePerson(options.participant);
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

  const args: Record<string, unknown> = { ...(params ?? {}) };
  if (options.participant !== undefined) {
    if (args.from !== undefined && args.from !== options.participant) {
      return {
        jsonrpc: "2.0",
        id,
        error: {
          code: -32011,
          message: `This server acts as ${options.participant}; a call cannot name another from (${JSON.stringify(args.from)}).`,
        },
      };
    }
    args.from = options.participant;
  }

  return coord.dispatch({
    jsonrpc: "2.0",
    id,
    method,
    params: args,
  });
}

// ============================================================
//   Internals
// ============================================================

/**
 * Refuse a `human:` participant, as the commands refuse one: a server that
 * makes every call as one member is a connection for an agent, and a
 * person decides in a desk.
 */
function refusePerson(participant: string | undefined): void {
  if (participant?.startsWith("human:")) {
    throw new Error(`participant ${participant} names a person. This server acts for an agent, and a person ` +
      "decides in a desk; give the agent's own URI, such as agent:orchestrator.");
  }
}

/** A part carrying JSON, with the media type the card names for every skill. */
function dataPart(data: unknown): Part {
  return { content: { $case: "data", value: data }, metadata: undefined, filename: "", mediaType: CHAP_MEDIA_TYPE };
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
