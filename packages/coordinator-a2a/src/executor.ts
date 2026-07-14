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

import type {
  AgentExecutor,
  ExecutionEventBus,
  RequestContext,
} from "@a2a-js/sdk/server";
import type {
  DataPart,
  Message,
  Task,
  TaskStatusUpdateEvent,
} from "@a2a-js/sdk";

import type { Coordinator, Envelope } from "@brightbeamai/chap-coordinator";
import { methodForTool } from "@brightbeamai/chap-coordinator-mcp/schemas";

export interface ChapAgentExecutorOptions {
  /** Optional id generator for CHAP envelope ids. */
  envelopeIdFactory?: () => string;
}

export class ChapAgentExecutor implements AgentExecutor {
  private readonly cancelled = new Set<string>();
  private readonly nextEnvelopeId: () => string;

  constructor(
    private readonly coord: Coordinator,
    options: ChapAgentExecutorOptions = {},
  ) {
    let counter = 0;
    this.nextEnvelopeId = options.envelopeIdFactory ?? (() => `a2a-${++counter}`);
  }

  async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    const message = requestContext.userMessage;
    const taskId = requestContext.taskId || randomUUID();
    const contextId = requestContext.contextId || message.contextId || randomUUID();

    if (this.cancelled.has(taskId)) {
      this.publishCanceled(eventBus, taskId, contextId);
      return;
    }

    const { skillId, params } = extractSkillAndParams(message);
    const method = skillId ? methodForTool(skillId) : null;

    if (!method) {
      this.publishErrorMessage(
        eventBus, taskId, contextId,
        `Unknown CHAP skill: ${skillId ?? "(missing)"}. ` +
        `Expected an A2A skill id of the form 'chap.<method>'.`,
        -32601,
      );
      eventBus.finished();
      return;
    }

    const envelope: Envelope = {
      jsonrpc: "2.0",
      id: this.nextEnvelopeId(),
      method,
      params: params ?? {},
    };

    let response: Envelope;
    try {
      response = this.coord.dispatch(envelope);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      this.publishErrorMessage(
        eventBus, taskId, contextId,
        `CHAP dispatch threw: ${msg}`, -32603,
      );
      eventBus.finished();
      return;
    }

    if (response.error) {
      this.publishErrorMessage(
        eventBus, taskId, contextId,
        response.error.message,
        response.error.code,
        response.error.data,
      );
    } else {
      this.publishSuccess(eventBus, taskId, contextId, response.result);
    }

    eventBus.finished();
  }

  async cancelTask(taskId: string, _eventBus: ExecutionEventBus): Promise<void> {
    this.cancelled.add(taskId);
  }

  // --- emit helpers --------------------------------------------------

  private publishSuccess(
    bus: ExecutionEventBus,
    taskId: string,
    contextId: string,
    result: unknown,
  ): void {
    const dataPart: DataPart = {
      kind: "data",
      data: this.coerceToRecord(result),
    };
    const msg: Message = {
      kind: "message",
      messageId: `chap-resp-${this.nextEnvelopeId()}`,
      role: "agent",
      contextId,
      taskId,
      parts: [dataPart],
    };
    bus.publish(msg);
  }

  private publishErrorMessage(
    bus: ExecutionEventBus,
    taskId: string,
    contextId: string,
    message: string,
    code: number,
    data?: unknown,
  ): void {
    const body: Record<string, unknown> = {
      chap_error: code,
      message,
    };
    if (data !== undefined) body.data = data;
    const part: DataPart = {
      kind: "data",
      data: body,
      metadata: { is_error: true },
    };
    const msg: Message = {
      kind: "message",
      messageId: `chap-resp-${this.nextEnvelopeId()}`,
      role: "agent",
      contextId,
      taskId,
      parts: [part],
    };
    bus.publish(msg);
  }

  private publishCanceled(
    bus: ExecutionEventBus,
    taskId: string,
    contextId: string,
  ): void {
    const evt: TaskStatusUpdateEvent = {
      kind: "status-update",
      taskId,
      contextId,
      status: {
        state: "canceled",
        timestamp: new Date().toISOString(),
      },
      final: true,
    };
    bus.publish(evt);
    bus.finished();
  }

  private coerceToRecord(value: unknown): Record<string, unknown> {
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
    return { result: value };
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

/**
 * Translate an A2A Message into a CHAP envelope and dispatch it
 * directly. Useful for tests and for embedding the adapter inside a
 * larger A2A server.
 */
export function dispatchA2aMessage(
  coord: Coordinator,
  message: Message,
  envelopeId: string | number = "a2a-call",
): Envelope {
  const { skillId, params } = extractSkillAndParams(message);
  const method = skillId ? methodForTool(skillId) : null;

  if (!method) {
    return {
      jsonrpc: "2.0",
      id: envelopeId,
      error: {
        code: -32601,
        message: `Unknown CHAP skill: ${skillId ?? "(missing)"}. ` +
                 `Expected an A2A skill id of the form 'chap.<method>'.`,
      },
    };
  }

  return coord.dispatch({
    jsonrpc: "2.0",
    id: envelopeId,
    method,
    params: params ?? {},
  });
}

// ============================================================
//   Internals
// ============================================================

/**
 * Pull the CHAP skill id and params out of an A2A Message. Looks at
 * the message metadata first (where the A2A client typically puts a
 * skill hint), then falls back to the first DataPart's data.
 */
function extractSkillAndParams(
  message: Message,
): { skillId: string | null; params: Record<string, unknown> | null } {
  let skillId: string | null = null;
  let params: Record<string, unknown> | null = null;

  const md = (message.metadata ?? {}) as Record<string, unknown>;
  if (typeof md.skill === "string") skillId = md.skill;

  for (const part of message.parts ?? []) {
    if (part.kind !== "data") continue;
    const blob = (part as DataPart).data ?? {};
    if (skillId === null && typeof blob.skill === "string") {
      skillId = blob.skill;
    }
    const blobParams = blob.params;
    if (blobParams && typeof blobParams === "object" && !Array.isArray(blobParams)) {
      params = blobParams as Record<string, unknown>;
    } else {
      // Treat the whole data blob as the params, minus the skill key.
      const { skill: _skill, ...rest } = blob;
      params = rest as Record<string, unknown>;
    }
    break;
  }

  return { skillId, params };
}
