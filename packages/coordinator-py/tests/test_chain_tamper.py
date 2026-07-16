"""
Tamper-evidence regression tests for audit.verify_chain.

The chain must detect tampering of any entry, including the last one, and
must not let an entry opt out of verification by dropping its prev_hash.
Regression guard for the two bugs fixed in 0.2.7 (missing head comparison
and the prev_hash opt-out).
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions


def _fresh():
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "review/1.0", "audit-scitt/1.0"]))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w", profiles=["core/1.0", "review/1.0", "audit-scitt/1.0"])
    s("participant.join", workspace="w", **{"from": "agent:bot"}, type="agent")
    s("task.create", workspace="w", **{"from": "agent:bot"}, task="t1", intent="first")
    s("task.create", workspace="w", **{"from": "agent:bot"}, task="t2", intent="last")
    return c, s


def test_legit_chain_verifies():
    c, s = _fresh()
    assert "result" in s("audit.verify_chain", workspace="w")


def test_tamper_middle_entry_caught():
    c, s = _fresh()
    c.workspaces["w"].audit[1].envelope["params"]["intent"] = "TAMPERED"
    assert "error" in s("audit.verify_chain", workspace="w")


def test_tamper_last_entry_caught_via_head_check():
    # No stored prev_hash covers the last entry, so this is caught only by
    # comparing the replayed head to the stored chain_head.
    c, s = _fresh()
    c.workspaces["w"].audit[-1].envelope["params"]["intent"] = "TAMPERED"
    assert "error" in s("audit.verify_chain", workspace="w")


def test_tamper_last_entry_with_dropped_prev_hash_caught():
    # Dropping prev_hash must not let the entry opt out of the check.
    c, s = _fresh()
    ws = c.workspaces["w"]
    ws.audit[-1].envelope["params"]["intent"] = "TAMPERED"
    ws.audit[-1].prev_hash = None
    assert "error" in s("audit.verify_chain", workspace="w")
