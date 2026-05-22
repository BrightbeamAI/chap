/**
 * @chap/coordinator/index
 *
 * The Coordinator class. CHAP protocol logic, packaged as a
 * library rather than a CLI server. Applications instantiate one,
 * call `dispatch(envelope)`, subscribe to audit events, and persist
 * via the provided `snapshot()` / `restore()` hooks.
 *
 * This module is deliberately UI-agnostic, transport-agnostic, and
 * persistence-agnostic. It owns the protocol semantics and nothing
 * else.
 */

import { E, rpcError } from "./jsonrpc.js";
import { applyJsonPatch } from "./patch.js";
import type {
  ArtefactId,
  AuditEntry,
  AuditListener,
  Envelope,
  Member,
  OverrideArtefact,
  ParticipantUri,
  RouteDecisionArtefact,
  Task,
  TaskId,
  TaskState,
  Workspace,
  WorkspaceId,
} from "./types.js";
import {
  type RoutingPolicy,
  type ReviewDepth,
} from "./routing.js";

export * from "./types.js";
export * from "./jsonrpc.js";
export * from "./patch.js";
export * from "./routing.js";

// ============================================================
//   ID generators (ULID-ish; deterministic-friendly seed for tests)
// ============================================================

const ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

function ulid(): string {
  // 10 chars of timestamp + 16 chars of randomness; not a real ULID
  // (no monotonicity guarantees) but the right shape for IDs.
  const ts = Date.now().toString(32).toUpperCase().padStart(10, "0");
  let rnd = "";
  for (let i = 0; i < 16; i++) {
    rnd += ULID_ALPHABET[Math.floor(Math.random() * 32)];
  }
  return ts + rnd;
}

function newTaskId(): TaskId         { return `tsk_${ulid()}`; }
function newArtefactId(): ArtefactId { return `art_${ulid()}`; }

function now(): string { return new Date().toISOString(); }

// ============================================================
//   Coordinator
// ============================================================

export interface CoordinatorOptions {
  /**
   * Routing policy used to decide review depth and auto-escalation.
   * If unset, no routing decisions are made; review/1.0 still works.
   */
  policy?: RoutingPolicy;
  /**
   * Called whenever the policy decides an auto-escalation is needed
   * (e.g. criticality=critical, or criticality=high + confidence<0.6).
   * The application is responsible for actually adding the escalation
   * target as a reviewer — the library only emits the decision.
   */
  onAutoEscalate?: (task: Task, to: ParticipantUri) => void;
}

export class Coordinator {
  private readonly workspaces = new Map<WorkspaceId, Workspace>();
  private readonly listeners:  AuditListener[] = [];
  private readonly options:    CoordinatorOptions;

  constructor(options: CoordinatorOptions = {}) {
    this.options = options;
  }

  // ---------------------------------------------------------
  //   Audit subscription
  // ---------------------------------------------------------

  onAudit(listener: AuditListener): () => void {
    this.listeners.push(listener);
    return () => {
      const i = this.listeners.indexOf(listener);
      if (i >= 0) this.listeners.splice(i, 1);
    };
  }

  // ---------------------------------------------------------
  //   Public read accessors (the playground's API uses these)
  // ---------------------------------------------------------

  getWorkspace(id: string): Workspace | null {
    return this.workspaces.get(id) ?? null;
  }

  listWorkspaces(): Workspace[] {
    return Array.from(this.workspaces.values());
  }

  // ---------------------------------------------------------
  //   Persistence
  // ---------------------------------------------------------

  snapshot(): unknown {
    return Array.from(this.workspaces.entries()).map(([id, ws]) => ({
      id,
      created: ws.created,
      state:   ws.state,
      members: Array.from(ws.members.values()),
      tasks:   Array.from(ws.tasks.values()),
      overrides: Array.from(ws.overrides.values()),
      route_decisions: Array.from(ws.route_decisions.values()),
      audit:   ws.audit,
      profiles: ws.profiles,
    }));
  }

  restore(data: unknown): void {
    if (!Array.isArray(data)) return;
    this.workspaces.clear();
    for (const ws of data) {
      const w: Workspace = {
        id:        ws.id,
        created:   ws.created,
        state:     ws.state,
        members:   new Map(),
        tasks:     new Map(),
        overrides: new Map(),
        route_decisions: new Map(),
        audit:     ws.audit ?? [],
        profiles:  ws.profiles ?? [],
      };
      for (const m of ws.members ?? [])   w.members.set(m.uri, m);
      for (const t of ws.tasks ?? [])     w.tasks.set(t.id, t);
      for (const o of ws.overrides ?? []) w.overrides.set(o.id, o);
      for (const r of ws.route_decisions ?? []) w.route_decisions.set(r.id, r);
      this.workspaces.set(w.id, w);
    }
  }

  // ---------------------------------------------------------
  //   Dispatch — the protocol entry point
  // ---------------------------------------------------------

  dispatch(env: Envelope): Envelope {
    if (env.jsonrpc !== "2.0" || !env.method) {
      return reply(env, { error: rpcError(E.REQUEST, "Invalid envelope") });
    }

    try {
      switch (env.method) {
        // Core
        case "workspace.create":       return this.opWorkspaceCreate(env);
        case "workspace.describe":     return this.opWorkspaceDescribe(env);
        case "workspace.set_profiles": return this.opWorkspaceSetProfiles(env);
        case "participant.join":       return this.opParticipantJoin(env);
        case "participant.leave":      return this.opParticipantLeave(env);
        case "task.create":            return this.opTaskCreate(env);
        case "task.update":            return this.opTaskUpdate(env);
        case "task.complete":          return this.opTaskComplete(env);
        case "audit.read":             return this.opAuditRead(env);

        // review/1.0
        case "review.request":         return this.opReviewRequest(env);
        case "decide.approve":         return this.opDecideApprove(env);
        case "decide.reject":          return this.opDecideReject(env);
        case "decide.override":        return this.opDecideOverride(env);
        case "abstain.declare":        return this.opAbstainDeclare(env);
        case "escalate.raise":         return this.opEscalateRaise(env);

        default:
          return reply(env, { error: rpcError(E.METHOD, `Unknown method: ${env.method}`) });
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return reply(env, { error: rpcError(E.INTERNAL, msg) });
    }
  }

  // ---------------------------------------------------------
  //   Audit append (with listener fan-out)
  // ---------------------------------------------------------

  private appendAudit(ws: Workspace, env: Envelope): AuditEntry {
    const entry: AuditEntry = {
      seq:     ws.audit.length,
      arrived: now(),
      envelope: env,
    };
    ws.audit.push(entry);
    for (const l of this.listeners) {
      try { l(ws, entry); } catch { /* listener errors don't break dispatch */ }
    }
    return entry;
  }

  // ---------------------------------------------------------
  //   Core ops
  // ---------------------------------------------------------

  private opWorkspaceCreate(env: Envelope): Envelope {
    const p = env.params as any;
    if (!p?.workspace_id) return reply(env, { error: rpcError(E.PARAMS, "workspace_id required") });
    if (this.workspaces.has(p.workspace_id)) {
      return reply(env, { error: rpcError(E.PARAMS, "workspace exists") });
    }
    const ws: Workspace = {
      id:        p.workspace_id,
      created:   now(),
      state:     "active",
      members:   new Map(),
      tasks:     new Map(),
      overrides: new Map(),
      route_decisions: new Map(),
      audit:     [],
      profiles:  p.profiles ?? ["core/1.0"],
    };
    this.workspaces.set(ws.id, ws);
    this.appendAudit(ws, env);
    return reply(env, { result: { workspace_id: ws.id, created: ws.created } });
  }

  private opWorkspaceDescribe(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    return reply(env, {
      result: {
        workspace_id: ws.id,
        created:      ws.created,
        state:        ws.state,
        profiles:     ws.profiles,
        members:      Array.from(ws.members.values()),
        evidence_head: ws.audit.length,
      },
    });
  }

  private opWorkspaceSetProfiles(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    if (!Array.isArray(p.profiles)) {
      return reply(env, { error: rpcError(E.PARAMS, "profiles must be string[]") });
    }
    ws.profiles = p.profiles;
    this.appendAudit(ws, env);
    return reply(env, { result: { profiles: ws.profiles } });
  }

  private opParticipantJoin(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    if (!p.uri || !p.type) return reply(env, { error: rpcError(E.PARAMS, "uri and type required") });

    const m: Member = {
      uri:    p.uri,
      type:   p.type,
      role:   p.role ?? "member",
      joined: now(),
      display_name: p.display_name,
      capabilities: p.capabilities,
    };
    ws.members.set(m.uri, m);
    this.appendAudit(ws, env);
    return reply(env, { result: { uri: m.uri, joined: m.joined } });
  }

  private opParticipantLeave(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    if (!ws.members.has(p?.uri)) {
      return reply(env, { error: rpcError(E.PARAMS, "no such member") });
    }
    ws.members.delete(p.uri);
    this.appendAudit(ws, env);
    return reply(env, { result: { uri: p.uri, left: now() } });
  }

  private opTaskCreate(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    if (!p.assignee || !p.delegator) {
      return reply(env, { error: rpcError(E.PARAMS, "assignee and delegator required") });
    }

    const task: Task = {
      id:         newTaskId(),
      kind:       p.kind ?? "generic",
      state:      "created",
      assignee:   p.assignee,
      delegator:  p.delegator,
      input:      p.input ?? {},
      confidence: p.confidence,
      deadline:   p.deadline,
      created_at: now(),
      updated_at: now(),
      routing_hints: p.routing_hints,
      history: [{ ts: now(), from: p.delegator, state: "created" }],
    };
    ws.tasks.set(task.id, task);
    this.appendAudit(ws, env);
    return reply(env, { result: { task_id: task.id, state: task.state } });
  }

  private opTaskUpdate(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    const task = ws.tasks.get(p?.task_id);
    if (!task) return reply(env, { error: rpcError(E.PARAMS, "no such task") });

    const nextState: TaskState | undefined = p.state;
    if (nextState) {
      task.state = nextState;
      task.history.push({ ts: now(), from: p.from ?? "service:coord", state: nextState, note: p.note });
    }
    task.updated_at = now();
    this.appendAudit(ws, env);
    return reply(env, { result: { task_id: task.id, state: task.state } });
  }

  private opTaskComplete(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    const task = ws.tasks.get(p?.task_id);
    if (!task) return reply(env, { error: rpcError(E.PARAMS, "no such task") });

    task.output = p.output;
    task.confidence = p.confidence ?? task.confidence;
    task.artefact_routing_hints = p.routing_hints ?? task.artefact_routing_hints;
    task.state = p.review_requested ? "review_requested" : "completed";
    task.history.push({ ts: now(), from: task.assignee, state: task.state });
    task.updated_at = now();

    if (p.review_requested) {
      task.review = {
        requested_at: now(),
        requested_to: p.reviewers ?? [task.delegator],
        rule:         p.rule ?? "any_one_approves",
        deadline:     p.review_deadline,
        decisions:    [],
      };

      // Run the routing policy if one is configured. The Coordinator
      // emits a route_decision artefact recording the depth choice
      // and (if escalation fires) an additional one for the auto-
      // escalation. These are audit-visible.
      if (this.options.policy) {
        const depthDec = this.options.policy.decideReviewDepth(
          task.routing_hints,
          task.artefact_routing_hints,
        );
        this.recordRouteDecision(ws, task, "review.depth", depthDec.depth, depthDec);

        const escDec = this.options.policy.decideEscalation(
          task.routing_hints,
          task.artefact_routing_hints,
        );
        if (escDec.escalate && escDec.to) {
          this.recordRouteDecision(ws, task, "escalate.auto", { escalate: true, to: escDec.to }, escDec);
          // Add the escalation target as a reviewer (additive, not replacing).
          if (!task.review.requested_to.includes(escDec.to)) {
            task.review.requested_to.push(escDec.to);
          }
          this.options.onAutoEscalate?.(task, escDec.to);
        } else {
          this.recordRouteDecision(ws, task, "escalate.auto", { escalate: false }, escDec);
        }
      }
    }

    this.appendAudit(ws, env);
    return reply(env, { result: { task_id: task.id, state: task.state } });
  }

  private recordRouteDecision(
    ws: Workspace,
    task: Task,
    decision_type: RouteDecisionArtefact["decision_type"],
    outcome: unknown,
    rationale: { policy_id: string; summary?: string; hints_used?: string[] },
  ): void {
    const a: RouteDecisionArtefact = {
      id:            newArtefactId(),
      task_id:       task.id,
      decision_type,
      outcome,
      policy_id:     rationale.policy_id,
      hints_observed: {
        task:     task.routing_hints ?? {},
        artefact: task.artefact_routing_hints ?? {},
      },
      rationale: rationale.summary ?? "",
      ts: now(),
    };
    ws.route_decisions.set(a.id, a);
    task.route_decisions = task.route_decisions ?? [];
    task.route_decisions.push(a);
  }

  private opAuditRead(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });

    const fromSeq = typeof p.from_seq === "number" ? p.from_seq : 0;
    const limit   = typeof p.limit   === "number" ? p.limit   : 100;
    const entries = ws.audit.slice(fromSeq, fromSeq + limit);

    return reply(env, {
      result: {
        workspace_id:  ws.id,
        from_seq:      fromSeq,
        evidence_head: ws.audit.length,
        entries,
      },
    });
  }

  // ---------------------------------------------------------
  //   review/1.0 ops
  // ---------------------------------------------------------

  private opReviewRequest(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    const task = ws.tasks.get(p?.task_id);
    if (!task) return reply(env, { error: rpcError(E.PARAMS, "no such task") });
    if (task.state !== "review_requested" && task.state !== "in_progress") {
      return reply(env, { error: rpcError(E.NOT_REVIEWABLE, `task in state ${task.state}`) });
    }

    task.review = task.review ?? {
      requested_at: now(),
      requested_to: p.reviewers ?? [task.delegator],
      rule:         p.rule ?? "any_one_approves",
      deadline:     p.deadline,
      decisions:    [],
    };
    task.state = "review_requested";
    task.history.push({ ts: now(), from: p.from ?? task.assignee, state: "review_requested" });
    task.updated_at = now();
    this.appendAudit(ws, env);
    return reply(env, { result: { task_id: task.id } });
  }

  private opDecideApprove(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    const task = ws.tasks.get(p?.task_id);
    if (!task) return reply(env, { error: rpcError(E.PARAMS, "no such task") });
    if (!task.review) return reply(env, { error: rpcError(E.NOT_REVIEWABLE, "no review open") });

    if (!task.review.requested_to.includes(p.from)) {
      return reply(env, { error: rpcError(E.NOT_AUTHORISED, `${p.from} is not a reviewer`) });
    }

    task.review.decisions.push({
      reviewer: p.from,
      kind:     "approve",
      ts:       now(),
      comment:  p.comment,
    });
    if (this.reviewSatisfied(task)) {
      task.state = "completed";
      task.history.push({ ts: now(), from: p.from, state: "completed", note: "approved" });
    }
    task.updated_at = now();
    this.appendAudit(ws, env);
    return reply(env, { result: { task_id: task.id, state: task.state } });
  }

  private opDecideReject(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    const task = ws.tasks.get(p?.task_id);
    if (!task) return reply(env, { error: rpcError(E.PARAMS, "no such task") });
    if (!task.review) return reply(env, { error: rpcError(E.NOT_REVIEWABLE, "no review open") });
    if (!task.review.requested_to.includes(p.from)) {
      return reply(env, { error: rpcError(E.NOT_AUTHORISED, `${p.from} is not a reviewer`) });
    }

    task.review.decisions.push({
      reviewer: p.from,
      kind:     "reject",
      ts:       now(),
      comment:  p.reason,
    });
    task.state = "in_progress"; // sent back for revision
    task.history.push({ ts: now(), from: p.from, state: "in_progress", note: "rejected" });
    task.updated_at = now();
    this.appendAudit(ws, env);
    return reply(env, { result: { task_id: task.id, state: task.state } });
  }

  private opDecideOverride(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    const task = ws.tasks.get(p?.task_id);
    if (!task) return reply(env, { error: rpcError(E.PARAMS, "no such task") });
    if (!task.review) return reply(env, { error: rpcError(E.NOT_REVIEWABLE, "no review open") });
    if (!task.review.requested_to.includes(p.from)) {
      return reply(env, { error: rpcError(E.NOT_AUTHORISED, `${p.from} is not a reviewer`) });
    }
    if (!Array.isArray(p.diff)) {
      return reply(env, { error: rpcError(E.PARAMS, "diff (RFC 6902) required") });
    }
    if (typeof p.rationale !== "string" || p.rationale.length === 0) {
      return reply(env, { error: rpcError(E.PARAMS, "rationale required") });
    }

    let result: unknown;
    try {
      result = applyJsonPatch(task.output, p.diff);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return reply(env, { error: rpcError(E.PATCH_FAILED, msg) });
    }

    const artefact: OverrideArtefact = {
      id:                newArtefactId(),
      task_id:           task.id,
      reviewer:          p.from,
      based_on_artefact: task.output,
      diff:              p.diff,
      result,
      rationale:         p.rationale,
      tags:              Array.isArray(p.tags) ? p.tags : [],
      policy_refs:       Array.isArray(p.policy_refs) ? p.policy_refs : [],
      ts:                now(),
    };
    ws.overrides.set(artefact.id, artefact);

    task.review.decisions.push({
      reviewer: p.from,
      kind:     "override",
      ts:       now(),
      tags:     artefact.tags,
      override_artefact_id: artefact.id,
    });

    // The overridden version becomes the task's new output, so a
    // second-stage reviewer sees the post-override content.
    task.output = result;

    if (this.reviewSatisfied(task)) {
      task.state = "completed";
      task.history.push({ ts: now(), from: p.from, state: "completed", note: "override applied" });
    }
    task.updated_at = now();
    this.appendAudit(ws, env);
    return reply(env, {
      result: { task_id: task.id, state: task.state, override_artefact_id: artefact.id },
    });
  }

  private opAbstainDeclare(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    const task = ws.tasks.get(p?.task_id);
    if (!task) return reply(env, { error: rpcError(E.PARAMS, "no such task") });
    if (!task.review) return reply(env, { error: rpcError(E.NOT_REVIEWABLE, "no review open") });

    task.review.decisions.push({
      reviewer: p.from,
      kind:     "abstain",
      ts:       now(),
      comment:  p.reason,
      abstain_category: p.category,
    });
    this.appendAudit(ws, env);
    return reply(env, { result: { task_id: task.id, state: task.state } });
  }

  private opEscalateRaise(env: Envelope): Envelope {
    const p = env.params as any;
    const ws = this.workspaces.get(p?.workspace_id);
    if (!ws) return reply(env, { error: rpcError(E.PARAMS, "no such workspace") });
    const task = ws.tasks.get(p?.task_id);
    if (!task) return reply(env, { error: rpcError(E.PARAMS, "no such task") });
    if (!p.to) return reply(env, { error: rpcError(E.PARAMS, "to required") });

    task.review = task.review ?? {
      requested_at: now(),
      requested_to: [p.to],
      rule:         "any_one_approves",
      decisions:    [],
    };
    if (!task.review.requested_to.includes(p.to)) {
      task.review.requested_to.push(p.to);
    }
    task.state = "review_requested";
    task.history.push({ ts: now(), from: p.from, state: "review_requested", note: `escalated to ${p.to}` });
    task.updated_at = now();
    this.appendAudit(ws, env);
    return reply(env, { result: { task_id: task.id, escalated_to: p.to } });
  }

  // ---------------------------------------------------------
  //   helpers
  // ---------------------------------------------------------

  private reviewSatisfied(task: Task): boolean {
    if (!task.review) return false;
    const decs = task.review.decisions;
    const approvers = new Set(
      decs.filter((d) => d.kind === "approve" || d.kind === "override").map((d) => d.reviewer),
    );
    switch (task.review.rule) {
      case "any_one_approves":
        return approvers.size >= 1;
      case "all_approve":
        return task.review.requested_to.every((r) => approvers.has(r));
      default:
        // quorum:n
        const m = task.review.rule.match(/^quorum:(\d+)$/);
        if (m) return approvers.size >= parseInt(m[1], 10);
        return approvers.size >= 1;
    }
  }
}

// ============================================================
//   small helpers
// ============================================================

function reply(req: Envelope, body: Partial<Envelope>): Envelope {
  return { jsonrpc: "2.0", id: req.id, ...body };
}
