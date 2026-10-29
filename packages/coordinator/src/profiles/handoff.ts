/**
 * handoff/1.0 profile (profiles/handoff.md).
 *
 * Carries multiple tasks. Recipient is a single URI or a group:... URI.
 *
 * Error codes (spec S6):
 *   -32050 tasks not assigned to proposer
 *   -32051 already resolved
 *   -32052 recipient not a workspace member
 */
import type { Coordinator } from "../coordinator.js";
import { E, rpcError } from "../jsonrpc.js";
import type { Handoff, HandoffTaskItem } from "../types.js";

// Names a supplied id cannot take: JavaScript gives them special meaning as
// object keys, and the patch engine refuses them as path segments too.
const RESERVED_IDS = new Set(["__proto__", "constructor", "prototype"]);

export function registerHandoff(coord: Coordinator): void {
  coord.handlers.set("handoff.propose", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    for (const f of ["from", "to", "tasks"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    const proposer = p.from as string;
    const recipient = p.to as string;
    if (typeof recipient === "string" && !recipient.startsWith("group:")) {
      if (!ws.members.has(recipient)) {
        return { error: rpcError(E.HANDOFF_RECIPIENT_NOT_MEMBER,
          `Recipient ${recipient} is not a member`) };
      }
    }
    const tasksIn = p.tasks;
    if (!Array.isArray(tasksIn) || !tasksIn.length) {
      return { error: rpcError(E.PARAMS, "tasks must be a non-empty list") };
    }
    const proposed: HandoffTaskItem[] = [];
    for (const t of tasksIn as Array<Record<string, unknown>>) {
      const tid = t.task_id as string;
      if (!tid || !ws.tasks.has(tid)) {
        return { error: rpcError(E.PARAMS, `Unknown task in handoff: ${tid}`) };
      }
      const task = ws.tasks.get(tid)!;
      if (task.assignee !== proposer) {
        return { error: rpcError(E.HANDOFF_TASKS_NOT_ASSIGNED_TO_PROPOSER,
          `Task ${tid} is not currently assigned to ${proposer}`) };
      }
      proposed.push({
        task_id: tid,
        title: t.title as string | undefined,
        status_summary: t.status_summary as string | undefined,
        next_action: t.next_action as string | undefined,
        blockers: (t.blockers as string[] | undefined) ?? [],
      });
    }
    // A supplied id names a new handoff. One already in use was stored
    // over the existing object, which reopened, reset or replaced it.
    const suppliedHandoffId = p.handoff_id;
    if (suppliedHandoffId !== undefined && suppliedHandoffId !== null && suppliedHandoffId !== "") {
      if (typeof suppliedHandoffId !== "string") {
        return { error: rpcError(E.PARAMS, "handoff_id must be a string") };
      }
      // The id is a key in the stored state, and a JavaScript object given
      // the key __proto__ drops it, so the object would be lost on a restart.
      if (RESERVED_IDS.has(suppliedHandoffId)) {
        return { error: rpcError(E.PARAMS, `handoff_id cannot be ${JSON.stringify(suppliedHandoffId)}`) };
      }
      if (ws.handoffs.has(suppliedHandoffId)) {
        return { error: rpcError(E.PARAMS, `handoff_id already in use: ${suppliedHandoffId}`) };
      }
    }
    const id = (p.handoff_id as string) || coord.ids.handoffId();
    const ho: Handoff = {
      id,
      proposer,
      recipient,
      proposed_at: coord.now(),
      tasks: proposed,
      summary: p.summary as string | undefined,
      context_links: (p.context_links as string[] | undefined) ?? [],
      state: "proposed",
    };
    ws.handoffs.set(id, ho);
    return { result: { handoff_id: id, state: "proposed", task_ids: proposed.map(t => t.task_id) } };
  });

  coord.handlers.set("handoff.accept", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    for (const f of ["from", "handoff_id"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    const ho = ws.handoffs.get(p.handoff_id as string);
    if (!ho) return { error: rpcError(E.PARAMS, "Unknown handoff") };
    if (ho.state !== "proposed") {
      return { error: rpcError(E.HANDOFF_ALREADY_RESOLVED, `Handoff already ${ho.state}`) };
    }
    const acceptor = p.from as string;
    if (ho.recipient.startsWith("group:")) {
      if (!ws.members.has(acceptor)) {
        return { error: rpcError(E.HANDOFF_RECIPIENT_NOT_MEMBER,
          "Acceptor is not a workspace member") };
      }
    } else {
      if (acceptor !== ho.recipient) {
        return { error: rpcError(E.HANDOFF_RECIPIENT_NOT_MEMBER,
          "Acceptor is not the named recipient") };
      }
      if (!ws.members.has(acceptor)) {
        return { error: rpcError(E.HANDOFF_RECIPIENT_NOT_MEMBER,
          "Acceptor is not a workspace member") };
      }
    }
    // Accepting makes the acceptor the assignee. A paused participant is
    // assigned no new tasks (control/1.0), so it cannot take tasks this way.
    if (ws.members.get(acceptor)!.paused) {
      return { error: rpcError(E.CONTROL_WORKSPACE_PAUSED, `Acceptor ${acceptor} is paused`) };
    }
    // Omission accepts all proposed tasks; an empty selection is not a handoff.
    if (Array.isArray(p.accepted_task_ids) && p.accepted_task_ids.length === 0) {
      return { error: rpcError(E.PARAMS, "accepted_task_ids must not be empty") };
    }
    const validIds = new Set(ho.tasks.map(t => t.task_id));
    const acceptedIds: string[] = Array.isArray(p.accepted_task_ids)
      ? [...p.accepted_task_ids as string[]]
      : ho.tasks.map(t => t.task_id);
    for (const tid of acceptedIds) {
      if (!validIds.has(tid)) {
        return { error: rpcError(E.PARAMS, `Task ${tid} not in this handoff`) };
      }
    }
    const now = coord.now();
    for (const tid of acceptedIds) {
      const task = ws.tasks.get(tid)!;
      task.assignee = acceptor;
      task.updated_at = now;
      task.history.push({
        ts: now, from: acceptor, state: task.state,
        note: `handoff accepted from ${ho.proposer}`,
      });
    }
    ho.state = "accepted";
    ho.accepted_by = acceptor;
    ho.accepted_task_ids = [...acceptedIds];
    ho.accept_comment = p.comment as string | undefined;
    ho.resolved_at = now;
    return { result: { accepted: true, task_ids: [...acceptedIds], assignee: acceptor } };
  });

  coord.handlers.set("handoff.decline", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    for (const f of ["from", "handoff_id"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    const ho = ws.handoffs.get(p.handoff_id as string);
    if (!ho) return { error: rpcError(E.PARAMS, "Unknown handoff") };
    if (ho.state !== "proposed") {
      return { error: rpcError(E.HANDOFF_ALREADY_RESOLVED, `Handoff already ${ho.state}`) };
    }
    const decliner = p.from as string;
    ho.decline_reason = p.reason as string | undefined;
    ho.decline_suggested_target = p.suggested_target as string | undefined;
    if (!ho.recipient.startsWith("group:") && decliner === ho.recipient) {
      ho.state = "declined";
      ho.resolved_at = coord.now();
    }
    return { result: { recorded: true, state: ho.state } };
  });
}
