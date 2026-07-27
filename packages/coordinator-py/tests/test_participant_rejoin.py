"""
Regression: participant.join must not replace an existing member. A re-join
keeps the member's role, scopes and keys, refreshes only the verified identity
binding, and never accepts self-asserted jwks for an already-admitted URI.
Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _ready(**opts):
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, **opts))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "human:alice"}, type="human",
      role="reviewer", scopes=["approve"], jwks={"keys": [{"kid": "alice-1", "x": "A"}]})
    return c, s


def test_rejoin_keeps_role_scopes_and_key():
    c, s = _ready()
    s("participant.join", workspace="w", **{"from": "human:alice"}, type="human",
      role="owner", jwks={"keys": [{"kid": "attacker", "x": "E"}]})
    m = c.get_workspace("w").members["human:alice"]
    assert m.role == "reviewer"
    assert m.scopes == ["approve"]
    assert [k.kid for k in m.keys] == ["alice-1"]


def test_rejoin_ignores_self_asserted_jwks():
    c, s = _ready()
    s("participant.join", workspace="w", **{"from": "human:alice"}, type="human",
      jwks={"keys": [{"kid": "attacker", "x": "E"}]})
    m = c.get_workspace("w").members["human:alice"]
    assert all(k.kid != "attacker" for k in m.keys)


def test_rejoin_refreshes_verified_binding():
    claims = {"good": {"sub": "alice", "auth_time": 111}}
    c = Coordinator(CoordinatorOptions(deterministic_ids=True,
                                       verify_oidc_token=lambda t: claims.get(t)))

    def s(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": params})

    s("workspace.create", workspace="w")
    s("participant.join", workspace="w", **{"from": "human:alice"}, type="human")
    s("participant.join", workspace="w", **{"from": "human:alice"}, type="human", oidc_token="good")
    m = c.get_workspace("w").members["human:alice"]
    assert m.oidc_sub == "alice"
    assert m.oidc_auth_time == 111


def test_new_member_with_jwks_registers_keys():
    c, s = _ready()
    s("participant.join", workspace="w", **{"from": "agent:new"}, type="agent",
      jwks={"keys": [{"kid": "n1", "x": "N"}]})
    m = c.get_workspace("w").members["agent:new"]
    assert [k.kid for k in m.keys] == ["n1"]
