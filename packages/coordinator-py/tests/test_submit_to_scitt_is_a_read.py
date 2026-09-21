"""
Regression (#153): audit.submit_to_scitt does not append to the log it submits.

It reads the chain and sends it onward. Recording the submission grew the chain
and moved its head, so the receipt attested a chain one entry shorter than the
one the workspace then held.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions

PROFILES = ["core/1.0", "audit-scitt/1.0"]


def _send(coord, method, **params):
    return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": params})


def test_submitting_the_chain_does_not_extend_it():
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        enable_chain=True, default_profiles=PROFILES))
    _send(coord, "workspace.create", workspace="w", profiles=PROFILES)
    _send(coord, "participant.join", workspace="w", **{"from": "human:a"}, type="human")
    ws = coord.get_workspace("w")

    entries, head = len(ws.audit), ws.chain_head
    result = _send(coord, "audit.submit_to_scitt", workspace="w", **{"from": "human:a"})

    assert result["result"]["statements"], "the submission carried the chain"
    assert len(ws.audit) == entries
    assert ws.chain_head == head
    assert _send(coord, "audit.verify_chain", workspace="w")["result"]["ok"]
