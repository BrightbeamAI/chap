"""
Regression: a weighted_vote tally uses the weights the opener set at
deliberate.open, not a weight the voter puts on their own vote. A voter not in
the opener's map counts as 1.0. Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _open(rule, weights=None, participants=("human:a", "human:b")):
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    for u in participants:
        s("participant.join", workspace="w", **{"from": u}, type="human")
    opts = {"to": list(participants), "rule": rule}
    if weights is not None:
        opts["weights"] = weights
    did = s("deliberate.open", workspace="w", **{"from": participants[0]}, **opts)["result"]["deliberation_id"]
    return s, did


def test_self_declared_weight_is_ignored():
    s, did = _open("weighted_vote:2.0")
    s("deliberate.vote", workspace="w", **{"from": "human:a"}, deliberation_id=did, vote="yea", weight=999)
    r = s("deliberate.close", workspace="w", **{"from": "human:a"}, deliberation_id=did)
    assert r["result"]["outcome"] == "rejected"
    assert r["result"]["tally"]["yea"] == 1.0


def test_opener_weight_is_authoritative():
    s, did = _open("weighted_vote:2.0", weights={"human:a": 3.0})
    s("deliberate.vote", workspace="w", **{"from": "human:a"}, deliberation_id=did, vote="yea", weight=999)
    r = s("deliberate.close", workspace="w", **{"from": "human:a"}, deliberation_id=did)
    assert r["result"]["outcome"] == "approved"
    assert r["result"]["tally"]["yea"] == 3.0
