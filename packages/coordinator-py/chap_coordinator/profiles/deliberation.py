"""
chap_coordinator.profiles.deliberation

The deliberation/1.0 profile (profiles/deliberation.md).

Multi-party voting with quorum, weights, and vetoes.

Methods (all per spec):
  - deliberate.open
  - deliberate.comment   (notification per spec; recorded in audit)
  - deliberate.vote      (uses `vote` field per spec, not `choice`)
  - deliberate.close

Decision rules:
  any_one_approves | all_approve | quorum:N |
  weighted_vote:T | weighted_vote_with_veto:T

Error codes:
  -32030 voter not in participant list
  -32031 already voted (re-vote disabled)
  -32032 closed or lapsed
  -32033 unknown rule
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..jsonrpc import E, rpc_error
from ..types import Deliberation

if TYPE_CHECKING:
    from ..coordinator import Coordinator


def _parse_rule(rule: str) -> tuple[str, dict]:
    if ":" in rule:
        kind, arg = rule.split(":", 1)
        if kind == "quorum":
            return kind, {"n": int(arg)}
        if kind in ("weighted_vote", "weighted_vote_with_veto"):
            return kind, {"threshold": float(arg)}
    if rule in ("any_one_approves", "all_approve"):
        return rule, {}
    raise ValueError(f"Unknown rule: {rule}")


def _compute_outcome(delib: Deliberation) -> dict:
    kind, params = _parse_rule(delib.rule)

    yea = [v for v in delib.votes if v.get("vote") == "yea"]
    nay = [v for v in delib.votes if v.get("vote") == "nay"]
    vetoes = [v for v in delib.votes
              if v.get("veto_invoked") and (delib.veto or {}).get(v["voter"])]
    weights = delib.weights or {}

    if kind == "any_one_approves":
        passed = len(yea) >= 1
        return {"outcome": "approved" if passed else "rejected",
                "rule": delib.rule,
                "tally": {"yea": len(yea), "nay": len(nay)},
                "vetoes": [v["voter"] for v in vetoes]}

    if kind == "all_approve":
        passed = (len(yea) == len(delib.participants) and len(nay) == 0)
        return {"outcome": "approved" if passed else "rejected",
                "rule": delib.rule,
                "tally": {"yea": len(yea), "nay": len(nay)},
                "vetoes": [v["voter"] for v in vetoes]}

    if kind == "quorum":
        n = params["n"]
        cast = len(yea) + len(nay)
        if cast < n:
            return {"outcome": "rejected",
                    "reason": "quorum not met",
                    "rule": delib.rule,
                    "tally": {"yea": len(yea), "nay": len(nay)},
                    "vetoes": []}
        passed = len(yea) > len(nay)
        return {"outcome": "approved" if passed else "rejected",
                "rule": delib.rule,
                "tally": {"yea": len(yea), "nay": len(nay)},
                "vetoes": []}

    if kind == "weighted_vote":
        yea_w = sum(weights.get(v["voter"], v.get("weight", 1.0)) for v in yea)
        nay_w = sum(weights.get(v["voter"], v.get("weight", 1.0)) for v in nay)
        passed = yea_w >= params["threshold"]
        return {"outcome": "approved" if passed else "rejected",
                "rule": delib.rule,
                "tally": {"yea": yea_w, "nay": nay_w},
                "vetoes": []}

    if kind == "weighted_vote_with_veto":
        if vetoes:
            return {"outcome": "rejected",
                    "rule": delib.rule,
                    "tally": {"yea": 0.0, "nay": 0.0},
                    "vetoes": [v["voter"] for v in vetoes]}
        yea_w = sum(weights.get(v["voter"], v.get("weight", 1.0)) for v in yea)
        nay_w = sum(weights.get(v["voter"], v.get("weight", 1.0)) for v in nay)
        passed = yea_w >= params["threshold"]
        return {"outcome": "approved" if passed else "rejected",
                "rule": delib.rule,
                "tally": {"yea": yea_w, "nay": nay_w},
                "vetoes": []}

    return {"outcome": "error", "reason": f"unsupported rule {kind}"}


def register_deliberation(coord: "Coordinator") -> None:

    def deliberate_open(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        for f in ("from", "rule"):
            if f not in p:
                return {"error": rpc_error(E.PARAMS, f"Missing field: {f}")}
        # Participants: spec uses `to` (list); accept `participants` too
        # for clients that match the in-memory dataclass.
        participants = p.get("to") or p.get("participants")
        if isinstance(participants, str):
            participants = [participants]
        if not participants:
            return {"error": rpc_error(E.PARAMS,
                                       "deliberate.open needs 'to' (participants list)")}

        try:
            _parse_rule(p["rule"])
        except ValueError as exc:
            return {"error": rpc_error(E.DELIB_UNKNOWN_RULE, str(exc))}

        # Client-supplied id or generate
        did = p.get("deliberation_id") or coord.ids.deliberation_id()

        delib = Deliberation(
            id=did,
            task_id=p.get("task_id"),
            opener=p["from"],
            participants=list(participants),
            rule=p["rule"],
            question=p.get("question"),
            weights=p.get("weights"),
            veto=p.get("veto"),
            deadline=p.get("deadline"),
            opened_at=coord.now_iso(),
        )
        ws.deliberations[did] = delib
        return {"result": {"deliberation_id": did, "state": "open"}}

    def deliberate_comment(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        delib = ws.deliberations.get(p.get("deliberation_id", ""))
        if not delib:
            return {"error": rpc_error(E.PARAMS, "Unknown deliberation")}
        if delib.state != "open":
            return {"error": rpc_error(E.DELIB_CLOSED_OR_LAPSED,
                                       "Deliberation not open")}
        delib.comments.append({
            "ts": coord.now_iso(),
            "from": p.get("from", ""),
            "text": p.get("comment") or p.get("text", ""),
        })
        return {"result": {"accepted": True}}

    def deliberate_vote(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        delib = ws.deliberations.get(p.get("deliberation_id", ""))
        if not delib:
            return {"error": rpc_error(E.PARAMS, "Unknown deliberation")}
        if delib.state != "open":
            return {"error": rpc_error(E.DELIB_CLOSED_OR_LAPSED,
                                       "Deliberation not open")}
        for f in ("from", "vote"):
            if f not in p:
                return {"error": rpc_error(E.PARAMS, f"Missing field: {f}")}
        if p["vote"] not in ("yea", "nay", "abstain"):
            return {"error": rpc_error(E.PARAMS,
                                       "vote must be yea/nay/abstain")}
        voter = p["from"]
        if voter not in delib.participants:
            return {"error": rpc_error(E.DELIB_VOTER_NOT_IN_LIST,
                                       f"Voter {voter} not in participant list")}
        if any(v["voter"] == voter for v in delib.votes):
            return {"error": rpc_error(E.DELIB_ALREADY_VOTED,
                                       f"Voter {voter} has already voted")}
        delib.votes.append({
            "ts": coord.now_iso(),
            "voter": voter,
            "vote": p["vote"],
            "weight": p.get("weight"),
            "comment": p.get("comment"),
            "veto_invoked": bool(p.get("veto_invoked")),
        })
        return {"result": {"recorded": True}}

    def deliberate_close(p: dict) -> dict:
        ws = coord.workspaces.get(p.get("workspace", ""))
        if not ws:
            return {"error": rpc_error(E.PARAMS, "Unknown workspace")}
        delib = ws.deliberations.get(p.get("deliberation_id", ""))
        if not delib:
            return {"error": rpc_error(E.PARAMS, "Unknown deliberation")}
        if delib.state == "closed":
            assert delib.outcome is not None
            # Flatten outcome per spec example response shape
            return {"result": delib.outcome}
        delib.state = "closed"
        delib.outcome = _compute_outcome(delib)
        # Spec response shape: outcome is a string with sibling fields
        return {"result": delib.outcome}

    coord._handlers["deliberate.open"] = deliberate_open
    coord._handlers["deliberate.comment"] = deliberate_comment
    coord._handlers["deliberate.vote"] = deliberate_vote
    coord._handlers["deliberate.close"] = deliberate_close
