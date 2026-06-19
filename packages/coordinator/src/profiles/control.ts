/**
 * control/1.0 profile (profiles/control.md).
 *
 * Methods:
 *   - control.pause / control.resume   (scope: task|participant|workspace)
 *   - control.cancel
 *   - control.snapshot                  (returns art_... id)
 *   - control.rollback                  (to_snapshot_artefact_id + what_to_restore)
 *   - control.supersede                 (creates successor in one shot)
 *   - control.set_mode_ceiling
 */
import type { Coordinator } from "../coordinator.js";
import { E, rpcError } from "../jsonrpc.js";
import type { Mode, SnapshotArtefact, Task } from "../types.js";

const VALID_MODES = new Set<Mode>(["shadow", "trial", "production"]);
const VALID_SCOPES = new Set(["task", "participant", "workspace"]);

export function registerControl(coord: Coordinator): void {
  coord.handlers.set("control.pause", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const scope = (p.scope as string) || "task";
    if (!VALID_SCOPES.has(scope)) {
      return { error: rpcError(E.PARAMS, `scope must be task/participant/workspace`) };
    }
    if (scope === "task") {
      const task = ws.tasks.get(p.task_id as string);
      if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
      if (task.state === "completed" || task.state === "declined" || task.state === "cancelled") {
        return { error: rpcError(E.CONTROL_NOT_AUTHORISED, `Cannot pause ${task.state} task`) };
      }
      task.paused = true;
      const prior = task.state;
      task.state = "paused";
      task.updated_at = coord.now();
      task.history.push({ ts: task.updated_at, from: p.from as string, state: "paused", note: `was ${prior}` });
      return { result: { scope: "task", task_id: task.id, state: "paused" } };
    }
    if (scope === "participant") {
      const uri = (p.participant_uri as string) || (p.uri as string);
      if (!uri || !ws.members.has(uri)) return { error: rpcError(E.PARAMS, `Unknown participant: ${uri}`) };
      ws.members.get(uri)!.paused = true;
      return { result: {
        scope: "participant", participant_uri: uri, paused: true,
        in_flight_policy: (p.in_flight_policy as string) || "allow_to_complete",
      }};
    }
    // workspace
    ws.state = "paused";
    return { result: { scope: "workspace", workspace: ws.id, state: "paused" } };
  });

  coord.handlers.set("control.resume", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const scope = (p.scope as string) || "task";
    if (!VALID_SCOPES.has(scope)) {
      return { error: rpcError(E.PARAMS, `scope must be task/participant/workspace`) };
    }
    if (scope === "task") {
      const task = ws.tasks.get(p.task_id as string);
      if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
      if (task.state !== "paused") {
        return { error: rpcError(E.CONTROL_NOT_AUTHORISED, `Cannot resume ${task.state} task`) };
      }
      task.paused = false;
      task.state = "in_progress";
      task.updated_at = coord.now();
      task.history.push({ ts: task.updated_at, from: p.from as string, state: "in_progress" });
      return { result: { scope: "task", task_id: task.id, state: "in_progress" } };
    }
    if (scope === "participant") {
      const uri = (p.participant_uri as string) || (p.uri as string);
      if (!uri || !ws.members.has(uri)) return { error: rpcError(E.PARAMS, `Unknown participant: ${uri}`) };
      ws.members.get(uri)!.paused = false;
      return { result: { scope: "participant", participant_uri: uri, paused: false } };
    }
    ws.state = "active";
    return { result: { scope: "workspace", workspace: ws.id, state: "active" } };
  });

  coord.handlers.set("control.cancel", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    if (task.state === "completed" || task.state === "declined" || task.state === "cancelled") {
      return { error: rpcError(E.CONTROL_NOT_AUTHORISED, `Cannot cancel ${task.state} task`) };
    }
    task.state = "cancelled";
    task.updated_at = coord.now();
    task.history.push({ ts: task.updated_at, from: p.from as string, state: "cancelled", note: p.reason as string | undefined });
    return { result: { state: "cancelled" } };
  });

  coord.handlers.set("control.snapshot", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const include: string[] = (p.include as string[]) ?? ["members", "open_tasks", "mode_ceiling"];
    const state: Record<string, unknown> = {};
    if (include.includes("members")) {
      state.members = Array.from(ws.members.values()).map(m => ({
        uri: m.uri, type: m.type, role: m.role, scopes: m.scopes,
      }));
    }
    if (include.includes("open_tasks")) {
      state.open_tasks = Array.from(ws.tasks.values())
        .filter(t => t.state !== "completed" && t.state !== "declined" && t.state !== "cancelled")
        .map(t => ({ id: t.id, kind: t.kind, state: t.state, assignee: t.assignee }));
    }
    if (include.includes("mode_ceiling")) state.mode_ceiling = ws.mode_ceiling;
    if (include.includes("policy")) state.routing_policy_uri = ws.routing_policy_uri;
    if (include.includes("audit") || include.includes("all")) state.audit_seq = ws.audit.length;

    const snapId = coord.ids.artefactId();
    const snap: SnapshotArtefact = {
      id: snapId,
      kind: "snapshot",
      ts: coord.now(),
      by: p.from as string,
      workspace: ws.id,
      audit_seq: ws.audit.length,
      label: p.label as string | undefined,
      include,
      state,
    };
    ws.snapshots.set(snapId, snap);
    return { result: { snapshot_artefact_id: snapId, audit_seq: snap.audit_seq, artefact: snap } };
  });

  coord.handlers.set("control.rollback", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const snapId = (p.to_snapshot_artefact_id as string)
                 || (p.snapshot_artefact_id as string)
                 || (p.snapshot_id as string);
    if (!snapId) return { error: rpcError(E.PARAMS, "to_snapshot_artefact_id is required") };
    const snap = ws.snapshots.get(snapId);
    if (!snap) return { error: rpcError(E.CONTROL_SNAPSHOT_NOT_FOUND, `Unknown snapshot: ${snapId}`) };
    const what: string[] = (p.what_to_restore as string[]) ?? snap.include;
    const restored: string[] = [];
    if (what.includes("mode_ceiling") && snap.state.mode_ceiling) {
      ws.mode_ceiling = snap.state.mode_ceiling as Mode;
      restored.push("mode_ceiling");
    }
    if (what.includes("members") && Array.isArray(snap.state.members)) {
      const snapByUri = new Map<string, Record<string, unknown>>();
      for (const m of snap.state.members as Array<Record<string, unknown>>) {
        snapByUri.set(m.uri as string, m);
      }
      for (const [uri, snapM] of snapByUri) {
        if (ws.members.has(uri)) {
          const cur = ws.members.get(uri)!;
          cur.role = (snapM.role as string) ?? cur.role;
          cur.scopes = snapM.scopes as string[] | undefined;
        }
      }
      restored.push("members");
    }
    return { result: {
      rolled_back_to: snapId, audit_seq: snap.audit_seq, restored,
      reason: p.reason as string | undefined,
    }};
  });

  coord.handlers.set("control.supersede", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const oldId = p.task_id as string;
    const old = ws.tasks.get(oldId);
    if (!old) return { error: rpcError(E.PARAMS, "Unknown task") };
    const newSpec = (p.successor_task as Record<string, unknown>) ?? {};
    if (!newSpec.kind) {
      return { error: rpcError(E.PARAMS, "successor_task must include kind") };
    }
    const assignee = (newSpec.assignee as string) || old.assignee;
    if (!ws.members.has(assignee)) {
      return { error: rpcError(E.PARAMS, "successor assignee not in workspace") };
    }
    const now = coord.now();
    const newId = coord.ids.taskId();
    const newTask: Task = {
      id: newId,
      kind: newSpec.kind as string,
      state: "created",
      assignee,
      delegator: p.from as string,
      input: (newSpec.input as Record<string, unknown>) ?? {},
      created_at: now,
      updated_at: now,
      mode: (newSpec.mode as Mode) ?? old.mode,
      supersedes: old.id,
      history: [{ ts: now, from: p.from as string, state: "created",
                  note: `supersedes ${old.id}: ${(p.reason as string) || ""}` }],
      paused: false,
    };
    ws.tasks.set(newId, newTask);
    old.state = "superseded";
    old.superseded_by = newId;
    old.updated_at = now;
    old.history.push({ ts: now, from: p.from as string, state: "superseded",
                       note: `-> ${newId}: ${(p.reason as string) || ""}` });
    return { result: { superseded_task_id: old.id, new_task_id: newId, reason: p.reason as string | undefined } };
  });

  coord.handlers.set("control.set_mode_ceiling", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const m = (p.new_ceiling as Mode) ?? (p.mode_ceiling as Mode);
    if (!VALID_MODES.has(m)) {
      return { error: rpcError(E.PARAMS, `new_ceiling must be shadow/trial/production`) };
    }
    ws.mode_ceiling = m;
    return { result: { mode_ceiling: ws.mode_ceiling, reason: p.reason as string | undefined } };
  });
}
