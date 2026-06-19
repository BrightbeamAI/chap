"""
chap_coordinator.profiles.routing

The routing/1.0 profile (profiles/routing.md).

Three decision methods that consume routing_hints and produce
route_decision artefacts:
  - task.route     -> pick an assignee from candidates
  - review.depth   -> decide skip / spot_check / full
  - escalate.auto  -> evaluate auto-escalation rules

Each call records a route_decision artefact in the evidence chain.
``task.route`` MUST update the task's assignee to the selected URI.

Operators register a custom policy via CoordinatorOptions; the
defaults below are a sensible starting point that uses
criticality + confidence.

Error codes:
  -32510 no eligible assignee
  -32511 routing policy violation
  -32512 auto-escalation triggered (informational)
  -32513 candidates_empty
  -32514 depth_not_applicable
  -32515 policy_unreachable
  -32516 escalation target unavailable
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..jsonrpc import E, rpc_error
from ..types import RouteDecisionArtefact, TaskHistoryEntry

if TYPE_CHECKING:
    from ..coordinator import Coordinator


_CRIT = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _default_depth(hints: dict) -> tuple[str, float | None, str]:
    """Return (depth, sampling_probability, summary)."""
    crit = hints.get("criticality", "medium")
    try:
        conf = float(hints.get("confidence", 1.0))
    except Exception:
        conf = 1.0
    crit_rank = _CRIT.get(crit, 1)
    if crit_rank >= 3:
        return "full", None, "criticality=critical: full review"
    if crit_rank == 2 and conf < 0.7:
        return "full", None, "criticality=high and confidence<0.7: full review"
    if crit_rank == 0 and conf >= 0.95:
        return "skip", None, "very low criticality + very high confidence: skip"
    if crit_rank <= 1 and conf >= 0.85:
        return "spot_check", 0.10, "low criticality + high confidence: 10% sample"
    return "spot_check", 0.25, "default mid-confidence: 25% sample"


def register_routing(coord: "Coordinator") -> None:

    def task_route(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        task_id = p.get("task_id")
        task = ws.tasks.get(task_id or "")
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        candidates = p.get("candidates") or []
        if not candidates:
            return {"error": rpc_error(E.ROUTING_CANDIDATES_EMPTY,
                                       "candidates array was empty")}

        # Operator-supplied policy takes precedence
        if coord.options.routing_policy is not None:
            try:
                decision = coord.options.routing_policy(task, list(candidates))
            except Exception as exc:
                return {"error": rpc_error(E.ROUTING_POLICY_UNREACHABLE,
                                           f"routing policy error: {exc}")}
            selected = decision.get("selected")
            rationale = decision.get("rationale") or {}
        else:
            # Default: pick first eligible
            eligible = [c for c in candidates if c in ws.members]
            if not eligible:
                return {"error": rpc_error(E.ROUTING_NO_ELIGIBLE_ASSIGNEE,
                                           "No candidate is a workspace member")}
            selected = eligible[0]
            rationale = {
                "policy_id": "default",
                "hints_used": list((task.routing_hints or {}).keys()),
                "summary": "default policy: first eligible candidate",
                "alternatives_considered": [
                    {"candidate": c, "reason_excluded": "not first eligible"}
                    for c in eligible[1:]
                ],
            }

        if selected not in ws.members:
            return {"error": rpc_error(E.ROUTING_NO_ELIGIBLE_ASSIGNEE,
                                       f"Selected {selected!r} is not a member")}

        # Update assignee per spec S3
        task.assignee = selected
        task.updated_at = coord.now_iso()
        task.history.append(TaskHistoryEntry(
            ts=task.updated_at, from_="service:coordinator",
            state=task.state, note=f"routed to {selected}",
        ))

        art_id = coord.ids.artefact_id()
        artefact = RouteDecisionArtefact(
            id=art_id,
            decision_type="task.route",
            outcome=selected,
            produced_by="service:coordinator",
            produced_at=task.updated_at,
            task=task.id,
            policy_id=rationale.get("policy_id"),
            hints_observed=dict(task.routing_hints or {}),
            rationale=rationale.get("summary", ""),
            extra={"alternatives_considered": rationale.get("alternatives_considered", [])},
        )
        ws.route_decisions[art_id] = artefact
        return {"result": {
            "selected": selected,
            "decision_artefact": art_id,
            "rationale": rationale,
        }}

    def review_depth(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        task_id = p.get("task_id")
        task = ws.tasks.get(task_id or "")
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        hints = dict(task.routing_hints or {})
        hints.update(p.get("artefact_routing_hints") or {})
        if not hints:
            return {"error": rpc_error(E.ROUTING_DEPTH_NOT_APPLICABLE,
                                       "No routing_hints to consult")}

        if coord.options.review_depth_policy is not None:
            try:
                d = coord.options.review_depth_policy(task, hints)
            except Exception as exc:
                return {"error": rpc_error(E.ROUTING_POLICY_UNREACHABLE,
                                           f"depth policy error: {exc}")}
            depth = d.get("depth", "full")
            sampling = d.get("sampling_probability")
            summary = (d.get("rationale") or {}).get("summary", "operator policy")
            policy_id = (d.get("rationale") or {}).get("policy_id", "operator")
        else:
            depth, sampling, summary = _default_depth(hints)
            policy_id = "default"

        art_id = coord.ids.artefact_id()
        artefact = RouteDecisionArtefact(
            id=art_id,
            decision_type="review.depth",
            outcome=depth,
            produced_by="service:coordinator",
            produced_at=coord.now_iso(),
            task=task.id,
            policy_id=policy_id,
            hints_observed=hints,
            rationale=summary,
            extra={"sampling_probability": sampling} if sampling is not None else {},
        )
        ws.route_decisions[art_id] = artefact
        result: dict = {
            "depth": depth,
            "decision_artefact": art_id,
            "rationale": {
                "policy_id": policy_id,
                "hints_used": list(hints.keys()),
                "summary": summary,
            },
        }
        if sampling is not None:
            result["sampling_probability"] = sampling
        return {"result": result}

    def escalate_auto(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        task_id = p.get("task_id")
        task = ws.tasks.get(task_id or "")
        if not task:
            return {"error": rpc_error(E.PARAMS, "Unknown task")}
        hints = dict(task.routing_hints or {})

        if coord.options.escalation_policy is not None:
            try:
                d = coord.options.escalation_policy(task, hints)
            except Exception as exc:
                return {"error": rpc_error(E.ROUTING_POLICY_UNREACHABLE,
                                           f"escalation policy error: {exc}")}
            escalate = bool(d.get("escalate"))
            to = d.get("to")
            rule = d.get("triggered_rule") or {}
        else:
            # Default: escalate when criticality is critical, OR
            # criticality is high AND confidence < 0.6
            crit = hints.get("criticality", "medium")
            try:
                conf = float(hints.get("confidence", 1.0))
            except Exception:
                conf = 1.0
            crit_rank = _CRIT.get(crit, 1)
            escalate = (crit_rank >= 3) or (crit_rank >= 2 and conf < 0.6)
            to = p.get("default_escalation_target")
            rule = {
                "rule_id": "default-auto-esc",
                "summary": "criticality=critical OR (criticality>=high AND confidence<0.6)",
                "hints_used": ["criticality", "confidence"],
            }

        art_id = coord.ids.artefact_id()
        artefact = RouteDecisionArtefact(
            id=art_id,
            decision_type="escalate.auto",
            outcome={"escalate": True, "to": to} if escalate else {"escalate": False},
            produced_by="service:coordinator",
            produced_at=coord.now_iso(),
            task=task.id,
            policy_id=rule.get("rule_id"),
            hints_observed=hints,
            rationale=rule.get("summary", ""),
            extra={"escalation_target": to} if escalate else {},
        )
        ws.route_decisions[art_id] = artefact

        if escalate:
            if not to:
                return {"error": rpc_error(E.ROUTING_ESC_TARGET_UNAVAILABLE,
                                           "Escalation triggered but no target available")}
            if not to.startswith("group:") and to not in ws.members:
                return {"error": rpc_error(E.ROUTING_ESC_TARGET_UNAVAILABLE,
                                           f"Escalation target {to} is not a member or group")}
            if coord.options.on_auto_escalate:
                try:
                    coord.options.on_auto_escalate(task, to)
                except Exception:
                    pass
            return {"result": {
                "escalate": True,
                "to": to,
                "decision_artefact": art_id,
                "triggered_rule": rule,
            }}
        return {"result": {
            "escalate": False,
            "decision_artefact": art_id,
        }}

    coord._handlers["task.route"] = task_route
    coord._handlers["review.depth"] = review_depth
    coord._handlers["escalate.auto"] = escalate_auto
