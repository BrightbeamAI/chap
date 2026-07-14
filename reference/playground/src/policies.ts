/**
 * Playground-specific routing policies.
 *
 * The library ships a sensible default in profiles/routing.ts. The
 * playground overrides those defaults so the policy ids in the audit
 * log identify the policy by name ("playground-default-v1") and the
 * escalation target is the workspace's senior reviewer (Sam).
 */
import type {
  EscalationPolicyFn,
  ReviewDepthPolicyFn,
  RoutingPolicyFn,
} from "@brightbeamai/chap-coordinator";

const CRIT: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

export function makePlaygroundPolicies(seniorTarget: string): {
  routingPolicy:     RoutingPolicyFn;
  reviewDepthPolicy: ReviewDepthPolicyFn;
  escalationPolicy:  EscalationPolicyFn;
} {
  const POLICY_ID = "playground-default-v1";

  const routingPolicy: RoutingPolicyFn = (_task, candidates) => ({
    selected: candidates[0],
    rationale: {
      policy_id: POLICY_ID,
      summary: "first eligible candidate",
      alternatives_considered: candidates.slice(1).map((c) => ({
        candidate: c, reason_excluded: "not first eligible",
      })),
    },
  });

  const reviewDepthPolicy: ReviewDepthPolicyFn = (_task, hints) => {
    const crit = (hints.criticality as string) ?? "medium";
    const conf = typeof hints.confidence === "number" ? hints.confidence : 1.0;
    const r = CRIT[crit] ?? 1;
    // critical -> full
    if (r >= 3) return { depth: "full",
      rationale: { policy_id: POLICY_ID, summary: "criticality=critical: full review" }};
    // high + low confidence -> full
    if (r === 2 && conf < 0.7) return { depth: "full",
      rationale: { policy_id: POLICY_ID, summary: "criticality=high and confidence<0.7: full review" }};
    // confidence>=0.85 -> spot_check at 10%
    if (conf >= 0.85) return { depth: "spot_check", sampling_probability: 0.10,
      rationale: { policy_id: POLICY_ID, summary: "high confidence: 10% sample" }};
    // default mid-confidence -> spot_check at 25%
    return { depth: "spot_check", sampling_probability: 0.25,
      rationale: { policy_id: POLICY_ID, summary: "default: 25% sample" }};
  };

  const escalationPolicy: EscalationPolicyFn = (_task, hints) => {
    const crit = (hints.criticality as string) ?? "medium";
    const conf = typeof hints.confidence === "number" ? hints.confidence : 1.0;
    const r = CRIT[crit] ?? 1;
    // critical OR (high AND confidence<0.6) -> escalate
    const shouldEscalate = (r >= 3) || (r >= 2 && conf < 0.6);
    return {
      escalate: shouldEscalate,
      to: shouldEscalate ? seniorTarget : undefined,
      triggered_rule: {
        rule_id: POLICY_ID + "/esc-1",
        summary: "criticality=critical OR (high AND confidence<0.6)",
        hints_used: ["criticality", "confidence"],
      },
    };
  };

  return { routingPolicy, reviewDepthPolicy, escalationPolicy };
}
