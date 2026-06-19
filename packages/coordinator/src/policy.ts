/**
 * Default routing policy factory.
 *
 * Returns a partial CoordinatorOptions configured with the three
 * routing hooks (routingPolicy, reviewDepthPolicy, escalationPolicy)
 * that the playground and demo use out of the box. Operators can
 * override individual hooks by passing their own functions in
 * CoordinatorOptions.
 *
 * The defaults shipped with the Coordinator (in profiles/routing.ts)
 * implement the same logic; this factory exists so that the legacy
 * call site can keep using:
 *
 *   new Coordinator({ ...makeDefaultPolicy(SENIOR_URI), ... })
 *
 * The seniorTarget argument is the URI auto-escalation routes to
 * when no other target is supplied.
 */
import type {
  EscalationPolicyFn,
  ReviewDepthPolicyFn,
  RoutingPolicyFn,
  CoordinatorOptions,
} from "./coordinator.js";

const CRIT: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

export function makeDefaultPolicy(seniorTarget: string): Pick<CoordinatorOptions,
  "routingPolicy" | "reviewDepthPolicy" | "escalationPolicy"> {

  const routingPolicy: RoutingPolicyFn = (task, candidates) => {
    const eligible = candidates;  // delegate elig check to coordinator
    return {
      selected: eligible[0],
      rationale: {
        policy_id: "default",
        hints_used: Object.keys(task.routing_hints ?? {}),
        summary: "default policy: first eligible candidate",
        alternatives_considered: eligible.slice(1).map(c => ({
          candidate: c, reason_excluded: "not first eligible",
        })),
      },
    };
  };

  const reviewDepthPolicy: ReviewDepthPolicyFn = (_task, hints) => {
    const crit = (hints.criticality as string) ?? "medium";
    let conf = 1.0;
    try { conf = Number(hints.confidence ?? 1.0); } catch { conf = 1.0; }
    if (isNaN(conf)) conf = 1.0;
    const r = CRIT[crit] ?? 1;
    if (r >= 3) return { depth: "full",
      rationale: { policy_id: "default", summary: "criticality=critical: full review" }};
    if (r === 2 && conf < 0.7) return { depth: "full",
      rationale: { policy_id: "default", summary: "criticality=high and confidence<0.7: full review" }};
    if (r === 0 && conf >= 0.95) return { depth: "skip",
      rationale: { policy_id: "default", summary: "very low criticality + very high confidence: skip" }};
    if (r <= 1 && conf >= 0.85) return { depth: "spot_check", sampling_probability: 0.10,
      rationale: { policy_id: "default", summary: "low criticality + high confidence: 10% sample" }};
    return { depth: "spot_check", sampling_probability: 0.25,
      rationale: { policy_id: "default", summary: "default mid-confidence: 25% sample" }};
  };

  const escalationPolicy: EscalationPolicyFn = (_task, hints) => {
    const crit = (hints.criticality as string) ?? "medium";
    let conf = 1.0;
    try { conf = Number(hints.confidence ?? 1.0); } catch { conf = 1.0; }
    if (isNaN(conf)) conf = 1.0;
    const r = CRIT[crit] ?? 1;
    const escalate = (r >= 3) || (r >= 2 && conf < 0.6);
    return {
      escalate,
      to: escalate ? seniorTarget : undefined,
      triggered_rule: {
        rule_id: "default-auto-esc",
        summary: "criticality=critical OR (criticality>=high AND confidence<0.6)",
        hints_used: ["criticality", "confidence"],
      },
    };
  };

  return { routingPolicy, reviewDepthPolicy, escalationPolicy };
}
