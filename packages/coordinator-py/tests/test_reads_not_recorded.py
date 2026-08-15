"""
Regression: read-only methods (audit.read, workspace.describe,
audit.verify_chain, audit.verify_receipt) do not append to the audit chain.
Recording a read grew and re-linked the chain each time it was inspected.
Guards the 0.2.9 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _ready(**opts):
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, **opts))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    return c, s


def test_reads_do_not_grow_the_chain():
    c, s = _ready(enable_chain=True)
    n0 = len(c.get_workspace("w").audit)
    for _ in range(3):
        s("audit.read", workspace="w")
    s("workspace.describe", workspace="w")
    s("audit.verify_chain", workspace="w")
    assert len(c.get_workspace("w").audit) == n0


def test_writes_are_still_recorded():
    c, s = _ready()
    n0 = len(c.get_workspace("w").audit)
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human")
    assert len(c.get_workspace("w").audit) == n0 + 1
