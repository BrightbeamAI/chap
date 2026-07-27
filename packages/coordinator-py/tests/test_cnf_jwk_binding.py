"""
Regression: a self-asserted jwks is not co-registered for an identity-bound
participant. When an OIDC/VC join pins a cnf.jwk, that verifier-attested key is
the only signing key; a key the joiner also supplied is ignored so it cannot be
used to sign as that identity. Guards the 0.2.7 fix.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _verify_token(token):
    if token == "alice-token":
        return {"sub": "alice", "auth_time": 1747476000,
                "cnf": {"jwk": {"kty": "OKP", "crv": "Ed25519",
                                "kid": "device-key", "x": "DK"}}}
    return None


def test_self_asserted_jwks_ignored_when_oidc_bound():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True,
                                       verify_oidc_token=_verify_token))
    c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:alice", "type": "human",
                           "oidc_token": "alice-token",
                           "jwks": {"keys": [{"kty": "OKP", "crv": "Ed25519",
                                              "kid": "attacker", "x": "EVIL"}]}}})
    keys = [k.kid for k in c.workspaces["w"].members["human:alice"].keys]
    assert keys == ["device-key"]


def test_self_asserted_jwks_registered_without_binding():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))
    c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                "params": {"workspace": "w", "from": "agent:b", "type": "agent",
                           "jwks": {"keys": [{"kid": "k1", "x": "K"}]}}})
    keys = [k.kid for k in c.workspaces["w"].members["agent:b"].keys]
    assert keys == ["k1"]
