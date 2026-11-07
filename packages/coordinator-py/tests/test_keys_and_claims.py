"""
A rotation names a key of its own, and a token's auth_time is kept the same
way by both coordinators.

Keys are found by kid, so a rotation to a new key under the old key's kid
closed the new key with the old one and locked the member out. And an
auth_time with a fraction, or one that is a boolean, was kept as given by
one coordinator and as an integer by the other, so the two held different
member state and answered step-up differently. Mirrors
keys_and_claims.test.ts.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import derive_private_key, public_jwk, sign


def test_a_rotation_to_a_key_under_a_kid_the_member_holds_is_refused():
    c = Coordinator(CoordinatorOptions(require_signatures=True, deterministic_ids=True,
                                       deterministic_clock=True))
    n = [0]

    def signed(method, frm, key_seed=None, **params):
        n[0] += 1
        env = {"jsonrpc": "2.0", "id": f"c{n[0]}", "method": method,
               "params": {"workspace": "w", "from": frm, **params}}
        seed = key_seed or frm
        key = derive_private_key(seed)
        env["sig"] = sign(canonicalize(env), key, public_jwk(seed, key)["kid"])
        return c.dispatch(env)

    c.dispatch({"jsonrpc": "2.0", "id": "w", "method": "workspace.create",
                "params": {"workspace": "w", "from": "human:a"}})
    jwk = public_jwk("human:a", derive_private_key("human:a"))
    c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                "params": {"workspace": "w", "from": "human:a", "type": "human",
                           "jwks": {"keys": [jwk]}}})
    other = dict(public_jwk("human:a#2", derive_private_key("human:a#2")), kid=jwk["kid"])
    assert signed("participant.rotate_key", "human:a", old_kid=jwk["kid"], new_jwk=other)["error"] == {
        "code": -32602,
        "message": f"human:a already holds a key {jwk['kid']}; a new key takes a kid of its own"}
    assert "error" not in signed("workspace.describe", "human:a"), "the member still signs"


@pytest.mark.parametrize("claim, kept", [(1_700_000_000.75, 1_700_000_000), (True, None),
                                         (1_700_000_001, 1_700_000_001), (1e21, None),
                                         (2**53 + 1, None), (10**400, None)])
def test_auth_time_is_kept_in_whole_seconds_and_a_boolean_or_a_number_past_2_53_is_no_auth_time(claim, kept):
    c = Coordinator(CoordinatorOptions(
        deterministic_ids=True,
        verify_oidc_token=lambda t: {"sub": "s", "chap_participant_uri": "human:a",
                                     "auth_time": claim}))
    c.dispatch({"jsonrpc": "2.0", "id": "w", "method": "workspace.create",
                "params": {"workspace": "w", "from": "human:a"}})
    r = c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "w", "from": "human:a", "type": "human",
                               "oidc_token": "t"}})
    assert "error" not in r, r
    assert c.workspaces["w"].members["human:a"].oidc_auth_time == kept
    assert type(c.workspaces["w"].members["human:a"].oidc_auth_time) in (int, type(None))
