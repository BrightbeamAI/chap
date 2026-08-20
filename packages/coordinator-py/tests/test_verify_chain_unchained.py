"""
Regression: audit.verify_chain must not report a clean log as tampered when the
workspace has no chain. An unchained workspace is refused, and a workspace whose
chain was enabled mid-life replays only from its first chained entry. Guards the
0.2.9 fix.

The mid-life case below asserted ok=True until issue #76. Replaying from the
first chained entry is still correct, but reporting a pass over a log whose
earlier entries were never evaluated is not: the verdict is now
"not_evaluated", and the refusal-versus-verdict distinction this file guards
is unchanged.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E


def _send(c):
    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})
    return s


def test_unchained_workspace_is_not_reported_tampered():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))
    s = _send(c)
    s("workspace.create", workspace="w", profiles=["core/1.0"])
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    r = s("audit.verify_chain", workspace="w")
    assert "error" in r
    assert r["error"]["code"] == E.PARAMS
    assert "Chain not enabled" in r["error"]["message"]


def test_chain_enabled_midlife_reports_the_uncovered_prefix_not_a_pass():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))
    s = _send(c)
    s("workspace.create", workspace="w", profiles=["core/1.0"])
    s("participant.join", workspace="w", **{"from": "human:a"}, type="human", role="admin")
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    s("task.create", workspace="w", **{"from": "human:a"}, kind="k", input={}, assignee="agent:b")
    s("workspace.set_profiles", workspace="w", **{"from": "human:a"},
      profiles=["core/1.0", "audit-scitt/1.0"])
    s("task.create", workspace="w", **{"from": "human:a"}, kind="k2", input={}, assignee="agent:b")
    v = s("audit.verify_chain", workspace="w")["result"]
    assert v["status"] == "not_evaluated", "entries predate the chain"
    assert v["ok"] is False
    assert v["reason"] == "unchained_prefix"
    assert v["entries_unchecked"] > 0


def test_chained_workspace_still_detects_tampering():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, enable_chain=True))
    s = _send(c)
    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "agent:b"}, type="agent")
    s("task.create", workspace="w", **{"from": "agent:b"}, task="t1")
    # Chained from genesis, so coverage is complete and a pass is correct.
    v = s("audit.verify_chain", workspace="w")["result"]
    assert v["status"] == "verified"
    assert v["ok"] is True
    assert v["entries_unchecked"] == 0
    c.get_workspace("w").audit[-1].envelope["params"]["task"] = "TAMPERED"
    assert "error" in s("audit.verify_chain", workspace="w")
