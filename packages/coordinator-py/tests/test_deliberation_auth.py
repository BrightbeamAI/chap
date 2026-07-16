"""
Regression: deliberation/1.0 open/close/comment require workspace membership,
so a non-member cannot open a deliberation (setting its rule/participants) or
close it to finalize the tally early. The per-voter eligibility and
double-vote checks are separate and still apply. Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _setup():
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "deliberation/1.0"]))

    def s(m, **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m, "params": p})

    s("workspace.create", workspace="w", profiles=["core/1.0", "deliberation/1.0"])
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human")
    s("participant.join", workspace="w", **{"from": "human:b"}, type="human")
    return c, s


def test_non_member_cannot_open_deliberation():
    c, s = _setup()
    r = s("deliberate.open", workspace="w", **{"from": "human:outsider"},
          to=["human:a", "human:b"], rule="all_approve")
    assert "error" in r and r["error"]["code"] == -32011


def test_non_member_cannot_close_deliberation():
    c, s = _setup()
    did = s("deliberate.open", workspace="w", **{"from": "human:a"},
            to=["human:a", "human:b"], rule="all_approve")["result"]["deliberation_id"]
    r = s("deliberate.close", workspace="w", **{"from": "human:outsider"}, deliberation_id=did)
    assert "error" in r and r["error"]["code"] == -32011


def test_member_voting_integrity_intact():
    c, s = _setup()
    did = s("deliberate.open", workspace="w", **{"from": "human:a"},
            to=["human:a", "human:b"], rule="all_approve")["result"]["deliberation_id"]
    assert "result" in s("deliberate.vote", workspace="w", **{"from": "human:a"}, deliberation_id=did, vote="yea")
    # double vote blocked
    assert "error" in s("deliberate.vote", workspace="w", **{"from": "human:a"}, deliberation_id=did, vote="nay")
    # non-participant (but also non-member) blocked at eligibility
    assert "error" in s("deliberate.vote", workspace="w", **{"from": "human:c"}, deliberation_id=did, vote="yea")
