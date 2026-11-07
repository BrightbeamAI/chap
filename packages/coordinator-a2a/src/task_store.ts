/**
 * @brightbeamai/chap-coordinator-a2a/task_store
 *
 * The task store a CHAP agent needs. The executor answers a call with a
 * message, and makes a task only to refuse one, a task that is final the
 * moment it is made. The SDK's InMemoryTaskStore keeps every task for as
 * long as the server runs, each with the request in its history, so every
 * refused call, up to the 1 MiB a request may carry, stayed in memory.
 */

import { Role, type ListTasksRequest, type ListTasksResponse, type Task } from "@a2a-js/sdk";
import { InMemoryTaskStore, resolveUserScope, type ServerCallContext, type TaskStore } from "@a2a-js/sdk/server";

/** How many tasks a ChapTaskStore keeps by default. */
export const DEFAULT_MAX_TASKS = 1000;

export interface ChapTaskStoreOptions {
  /** The most tasks kept; the oldest goes first. Default 1,000. */
  maxTasks?: number;
}

/**
 * A TaskStore that keeps the `maxTasks` most recently saved tasks, and
 * keeps each without the client's own messages. GetTask still answers for
 * a recent refusal, with its state and the agent's message that carries
 * the CHAP error, and memory stays bounded however many calls are refused.
 * Tasks are scoped by tenant and caller, as InMemoryTaskStore scopes them.
 */
export class ChapTaskStore implements TaskStore {
  private readonly maxTasks: number;
  /** Oldest first: a Map iterates in the order its keys were set. */
  private readonly tasks = new Map<string, { scope: string; task: Task }>();

  constructor(options: ChapTaskStoreOptions = {}) {
    this.maxTasks = options.maxTasks ?? DEFAULT_MAX_TASKS;
    if (!Number.isInteger(this.maxTasks) || this.maxTasks < 1) {
      throw new RangeError(`maxTasks takes a whole number of tasks, 1 or more; ${options.maxTasks} is not one`);
    }
  }

  async save(task: Task, context: ServerCallContext): Promise<void> {
    const scope = scopeOf(context);
    const key = `${scope}\u0000${task.id}`;
    const kept = structuredClone(task);
    kept.history = kept.history.filter((message) => message.role !== Role.ROLE_USER);
    // Saved again, a task becomes the newest.
    this.tasks.delete(key);
    this.tasks.set(key, { scope, task: kept });
    for (const oldest of this.tasks.keys()) {
      if (this.tasks.size <= this.maxTasks) break;
      this.tasks.delete(oldest);
    }
  }

  async load(taskId: string, context: ServerCallContext): Promise<Task | undefined> {
    const entry = this.tasks.get(`${scopeOf(context)}\u0000${taskId}`);
    return entry ? structuredClone(entry.task) : undefined;
  }

  /** The caller's tasks, filtered and paged by the SDK's own InMemoryTaskStore. */
  async list(params: ListTasksRequest, context: ServerCallContext): Promise<ListTasksResponse> {
    const scope = scopeOf(context);
    const view = new InMemoryTaskStore();
    for (const entry of this.tasks.values()) {
      if (entry.scope === scope) await view.save(entry.task, context);
    }
    return view.list(params, context);
  }
}

/** The tenant and the caller a task belongs to, as InMemoryTaskStore resolves them. */
function scopeOf(context: ServerCallContext): string {
  return `${context.tenant ?? ""}\u0000${resolveUserScope(context)}`;
}
