/**
 * routing/1.0 profile (profiles/routing.md).
 *
 * Three methods, each emits a route_decision artefact:
 *   - task.route      -> pick assignee + update task.assignee
 *   - review.depth    -> skip / spot_check / full + sampling_probability
 *   - escalate.auto   -> evaluate auto-escalation
 *
 * Error codes:
 *   -32510 no eligible assignee
 *   -32513 candidates_empty
 *   -32514 depth_not_applicable
 *   -32515 policy_unreachable
 *   -32516 escalation target unavailable
 */
import type { Coordinator } from "../coordinator.js";
import { E, rpcError } from "../jsonrpc.js";
import type { RouteDecisionArtefact, Task } from "../types.js";

const CRIT: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

function defaultDepth(hints: Record<string, unknown>): {
  depth: "skip" | "spot_check" | "full" | "escalated";
  sampling?: number;
  summary: string;
} {
  const crit = (hints.criticality as string) ?? "medium";
  let conf = 1.0;
  try { conf = Number(hints.confidence ?? 1.0); } catch { conf = 1.0; }
  if (isNaN(conf)) conf = 1.0;
  const r = CRIT[crit] ?? 1;
  if (r >= 3) return { depth: "full", summary: "criticality=critical: full review" };
  if (r === 2 && conf < 0.7) return { depth: "full", summary: "criticality=high and confidence<0.7: full review" };
  if (r === 0 && conf >= 0.95) return { depth: "skip", summary: "very low criticality + very high confidence: skip" };
  if (r <= 1 && conf >= 0.85) return { depth: "spot_check", sampling: 0.10, summary: "low criticality + high confidence: 10% sample" };
  return { depth: "spot_check", sampling: 0.25, summary: "default mid-confidence: 25% sample" };
}

export function registerRouting(coord: Coordinator): void {
  coord.handlers.set("task.route", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    const candidates = (p.candidates as string[]) ?? [];
    if (!candidates.length) {
      return { error: rpcError(E.ROUTING_CANDIDATES_EMPTY, "candidates array was empty") };
    }
    let selected: string;
    let rationale: Record<string, unknown>;
    if (coord.options.routingPolicy) {
      try {
        const d = coord.options.routingPolicy(task, [...candidates]);
        selected = d.selected;
        rationale = d.rationale;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return { error: rpcError(E.ROUTING_POLICY_UNREACHABLE, `routing policy error: ${msg}`) };
      }
    } else {
      const eligible = candidates.filter(c => ws.members.has(c));
      if (!eligible.length) {
        return { error: rpcError(E.ROUTING_NO_ELIGIBLE_ASSIGNEE, "No candidate is a workspace member") };
      }
      selected = eligible[0];
      rationale = {
        policy_id: "default",
        hints_used: Object.keys(task.routing_hints ?? {}),
        summary: "default policy: first eligible candidate",
        alternatives_considered: eligible.slice(1).map(c => ({
          candidate: c, reason_excluded: "not first eligible",
        })),
      };
    }
    if (!ws.members.has(selected)) {
      return { error: rpcError(E.ROUTING_NO_ELIGIBLE_ASSIGNEE,
        `Selected ${JSON.stringify(selected)} is not a member`) };
    }
    task.assignee = selected;
    task.updated_at = coord.now();
    task.history.push({ ts: task.updated_at, from: "service:coordinator",
                        state: task.state, note: `routed to ${selected}` });
    const artId = coord.ids.artefactId();
    const artefact: RouteDecisionArtefact = {
      id: artId, kind: "route_decision",
      decision_type: "task.route", outcome: selected,
      produced_by: "service:coordinator", produced_at: task.updated_at,
      task: task.id,
      policy_id: rationale.policy_id as string | undefined,
      hints_observed: { ...(task.routing_hints ?? {}) },
      rationale: (rationale.summary as string) ?? "",
      alternatives_considered: rationale.alternatives_considered,
    };
    ws.route_decisions.set(artId, artefact);
    return { result: { selected, decision_artefact: artId, rationale } };
  });

  coord.handlers.set("review.depth", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    const hints: Record<string, unknown> = { ...(task.routing_hints ?? {}),
                                              ...((p.artefact_routing_hints as Record<string, unknown>) ?? {}) };
    if (!Object.keys(hints).length) {
      return { error: rpcError(E.ROUTING_DEPTH_NOT_APPLICABLE, "No routing_hints to consult") };
    }
    let depth: "skip" | "spot_check" | "full" | "escalated";
    let sampling: number | undefined;
    let summary: string;
    let policyId: string;
    if (coord.options.reviewDepthPolicy) {
      try {
        const d = coord.options.reviewDepthPolicy(task, hints);
        depth = d.depth;
        sampling = d.sampling_probability;
        summary = (d.rationale.summary as string) ?? "operator policy";
        policyId = (d.rationale.policy_id as string) ?? "operator";
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return { error: rpcError(E.ROUTING_POLICY_UNREACHABLE, `depth policy error: ${msg}`) };
      }
    } else {
      const d = defaultDepth(hints);
      depth = d.depth; sampling = d.sampling; summary = d.summary;
      policyId = "default";
    }
    const artId = coord.ids.artefactId();
    const artefact: RouteDecisionArtefact = {
      id: artId, kind: "route_decision",
      decision_type: "review.depth", outcome: depth,
      produced_by: "service:coordinator", produced_at: coord.now(),
      task: task.id, policy_id: policyId,
      hints_observed: hints, rationale: summary,
      ...(sampling !== undefined ? { sampling_probability: sampling } : {}),
    };
    ws.route_decisions.set(artId, artefact);
    const result: Record<string, unknown> = {
      depth, decision_artefact: artId,
      rationale: { policy_id: policyId, hints_used: Object.keys(hints), summary },
    };
    if (sampling !== undefined) result.sampling_probability = sampling;
    return { result };
  });

  coord.handlers.set("escalate.auto", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    const hints = { ...(task.routing_hints ?? {}) };
    let escalate: boolean, to: string | undefined, rule: Record<string, unknown>;
    if (coord.options.escalationPolicy) {
      try {
        const d = coord.options.escalationPolicy(task, hints);
        escalate = !!d.escalate;
        to = d.to;
        rule = d.triggered_rule ?? {};
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return { error: rpcError(E.ROUTING_POLICY_UNREACHABLE, `escalation policy error: ${msg}`) };
      }
    } else {
      const crit = (hints.criticality as string) ?? "medium";
      let conf = 1.0;
      try { conf = Number(hints.confidence ?? 1.0); } catch { conf = 1.0; }
      if (isNaN(conf)) conf = 1.0;
      const r = CRIT[crit] ?? 1;
      escalate = r >= 3 || (r >= 2 && conf < 0.6);
      to = p.default_escalation_target as string | undefined;
      rule = {
        rule_id: "default-auto-esc",
        summary: "criticality=critical OR (criticality>=high AND confidence<0.6)",
        hints_used: ["criticality", "confidence"],
      };
    }
    const artId = coord.ids.artefactId();
    const artefact: RouteDecisionArtefact = {
      id: artId, kind: "route_decision",
      decision_type: "escalate.auto",
      outcome: escalate ? { escalate: true, to } : { escalate: false },
      produced_by: "service:coordinator", produced_at: coord.now(),
      task: task.id,
      policy_id: rule.rule_id as string | undefined,
      hints_observed: hints,
      rationale: (rule.summary as string) ?? "",
      ...(escalate ? { escalation_target: to } : {}),
    };
    ws.route_decisions.set(artId, artefact);
    if (escalate) {
      if (!to) return { error: rpcError(E.ROUTING_ESC_TARGET_UNAVAILABLE,
        "Escalation triggered but no target available") };
      if (!to.startsWith("group:") && !ws.members.has(to)) {
        return { error: rpcError(E.ROUTING_ESC_TARGET_UNAVAILABLE,
          `Escalation target ${to} is not a member or group`) };
      }
      if (coord.options.onAutoEscalate) {
        try { coord.options.onAutoEscalate(task, to); } catch { /* */ }
      }
      return { result: { escalate: true, to, decision_artefact: artId, triggered_rule: rule } };
    }
    return { result: { escalate: false, decision_artefact: artId } };
  });
}
