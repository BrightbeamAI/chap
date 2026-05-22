/**
 * @chap/coordinator/routing
 *
 * Routing policy interface. The Coordinator delegates to a
 * RoutingPolicy when deciding (a) how deeply to review an
 * artefact and (b) whether to auto-escalate.
 *
 * The policy reads `routing_hints` carried on tasks and artefacts
 * (see Core schema §8.4 and §9.5) and emits a decision. Decisions
 * are written back to the workspace as `route_decision` artefacts
 * — auditable, queryable, deterministic.
 *
 * This module ships a `defaultPolicy` suitable for the playground:
 * a hand-coded set of rules covering criticality + confidence. In a
 * full `routing/1.0` deployment this would be replaced by the
 * profile's `task.route` / `review.depth` / `escalate.auto` methods
 * backed by a real policy engine.
 */

import type {
  ArtefactRoutingHints,
  ParticipantUri,
  RouteDecisionArtefact,
  TaskRoutingHints,
} from "./types.js";

export type ReviewDepth = "skip" | "spot_check" | "full" | "escalated";

export interface ReviewDepthDecision {
  depth: ReviewDepth;
  sampling_probability?: number;
  policy_id: string;
  summary:   string;
  hints_used: string[];
}

export interface EscalationDecision {
  escalate:    boolean;
  policy_id:   string;
  to?:         ParticipantUri;
  rule_id?:    string;
  summary?:    string;
  hints_used?: string[];
}

export interface RoutingPolicy {
  policy_id: string;
  decideReviewDepth(
    taskHints: TaskRoutingHints | undefined,
    artefactHints: ArtefactRoutingHints | undefined,
  ): ReviewDepthDecision;
  decideEscalation(
    taskHints: TaskRoutingHints | undefined,
    artefactHints: ArtefactRoutingHints | undefined,
  ): EscalationDecision;
}

/**
 * The playground's default routing policy.
 *
 * Review depth:
 *   - criticality in {high, critical}   -> full
 *   - confidence < 0.7                  -> full
 *   - confidence >= 0.85                -> spot_check (p=0.10)
 *   - otherwise                         -> full (conservative default)
 *
 * Escalation (to senior reviewer):
 *   - criticality == critical                          -> escalate
 *   - criticality == high AND confidence < 0.6         -> escalate
 *   - otherwise                                        -> no escalate
 */
export function makeDefaultPolicy(seniorReviewerUri: ParticipantUri): RoutingPolicy {
  return {
    policy_id: "playground-default-v1",

    decideReviewDepth(taskHints, artefactHints) {
      const crit  = taskHints?.criticality;
      const conf  = artefactHints?.confidence;
      const used:  string[] = [];

      if (crit && (crit === "high" || crit === "critical")) {
        used.push("criticality");
        return {
          depth: "full",
          policy_id: "playground-default-v1",
          summary: `criticality=${crit} → full review`,
          hints_used: used,
        };
      }
      if (typeof conf === "number") {
        used.push("confidence");
        if (conf < 0.7) {
          return {
            depth: "full",
            policy_id: "playground-default-v1",
            summary: `confidence=${conf.toFixed(2)} below 0.7 → full review`,
            hints_used: used,
          };
        }
        if (conf >= 0.85) {
          return {
            depth: "spot_check",
            sampling_probability: 0.10,
            policy_id: "playground-default-v1",
            summary: `confidence=${conf.toFixed(2)} ≥ 0.85 → spot-check at p=0.10`,
            hints_used: used,
          };
        }
      }

      return {
        depth: "full",
        policy_id: "playground-default-v1",
        summary: "default conservative → full review",
        hints_used: used,
      };
    },

    decideEscalation(taskHints, artefactHints) {
      const crit = taskHints?.criticality;
      const conf = artefactHints?.confidence;
      const used: string[] = [];

      if (crit === "critical") {
        used.push("criticality");
        return {
          escalate: true,
          policy_id: "playground-default-v1",
          to: seniorReviewerUri,
          rule_id: "auto-esc-critical",
          summary: "criticality=critical → senior reviewer",
          hints_used: used,
        };
      }
      if (crit === "high" && typeof conf === "number" && conf < 0.6) {
        used.push("criticality", "confidence");
        return {
          escalate: true,
          policy_id: "playground-default-v1",
          to: seniorReviewerUri,
          rule_id: "auto-esc-high-low-confidence",
          summary: `criticality=high AND confidence=${conf.toFixed(2)} < 0.6 → senior reviewer`,
          hints_used: used,
        };
      }
      return { escalate: false, policy_id: "playground-default-v1" };
    },
  };
}

/**
 * Construct a route_decision artefact body. The Coordinator wraps
 * this with an id, ts, and task_id.
 */
export function makeRouteDecisionBody(
  decision_type: RouteDecisionArtefact["decision_type"],
  outcome: unknown,
  policy_id: string,
  hints_observed: Record<string, unknown>,
  rationale: string,
): Omit<RouteDecisionArtefact, "id" | "task_id" | "ts"> {
  return { decision_type, outcome, policy_id, hints_observed, rationale };
}
