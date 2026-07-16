"""
Regression: every control/1.0 operation requires workspace membership.
Without it a non-member could defeat the governance "emergency brake"
(resume a paused workspace, raise the mode ceiling, cancel tasks).
Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _setup():
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "control/1.0"]))

    def s(m, **p):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m, "params": p})

    s("workspace.create", workspace="w", profiles=["core/1.0", "control/1.0"])
    s("participant.join", workspace="w", **{"from": "human:gov"}, type="human")
    s("participant.join", workspace="w", **{"from": "agent:worker"}, type="agent")
    return c, s


def test_non_member_cannot_resume_paused_workspace():
    c, s = _setup()
    s("control.pause", workspace="w", **{"from": "human:gov"}, scope="workspace")
    r = s("control.resume", workspace="w", **{"from": "human:attacker"}, scope="workspace")
    assert "error" in r and r["error"]["code"] == -32011  # NOT_AUTHORISED
    assert c.workspaces["w"].state == "paused"  # still paused


def test_non_member_cannot_raise_mode_ceiling():
    c, s = _setup()
    r = s("control.set_mode_ceiling", workspace="w", **{"from": "human:attacker"}, new_ceiling="production")
    assert "error" in r and r["error"]["code"] == -32011


def test_member_can_perform_control_ops():
    c, s = _setup()
    s("control.pause", workspace="w", **{"from": "human:gov"}, scope="workspace")
    r = s("control.resume", workspace="w", **{"from": "human:gov"}, scope="workspace")
    assert "result" in r
