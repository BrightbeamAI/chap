"""
A self-asserted jwks is never registered for an identity-bound participant.
When an OIDC/VC join pins a cnf.jwk, that verifier-attested key is the only
signing key, and a join that brings another key is refused, so the key cannot
be used to sign as that identity.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _verify_token(token):
    if token == "alice-token":
        return {"sub": "alice", "chap_participant_uri": "human:alice", "auth_time": 1747476000,
                "cnf": {"jwk": {"kty": "OKP", "crv": "Ed25519",
                                "kid": "device-key", "x": "DK"}}}
    return None


def test_a_join_that_brings_a_key_other_than_the_one_its_token_binds_is_refused():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True,
                                       verify_oidc_token=_verify_token))

    def join(jwks=None):
        params = {"workspace": "w", "from": "human:alice", "type": "human",
                  "oidc_token": "alice-token"}
        if jwks is not None:
            params["jwks"] = jwks
        return c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                           "params": params})

    assert join({"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "attacker", "x": "EVIL"}]})[
        "error"] == {"code": -32404, "message": "The join's keys are not the key the token binds"}
    assert "error" not in join()
    keys = [k.kid for k in c.workspaces["w"].members["human:alice"].keys]
    assert keys == ["device-key"]


def test_self_asserted_jwks_registered_without_binding():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True))
    c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                "params": {"workspace": "w", "from": "agent:b", "type": "agent",
                           "jwks": {"keys": [{"kid": "k1", "x": "K"}]}}})
    keys = [k.kid for k in c.workspaces["w"].members["agent:b"].keys]
    assert keys == ["k1"]
