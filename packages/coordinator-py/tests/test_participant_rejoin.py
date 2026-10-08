"""
Regression: participant.join must not replace an existing member. A re-join
keeps the member's role, scopes and keys, refreshes only the verified identity
binding, and never accepts self-asserted jwks for an already-admitted URI.
Guards the 0.2.9 fix.

A verified token or presentation binds only to the participant it belongs to:
a re-join must carry the member's own subject or holder, or, for a member
with no verified subject, a token whose chap_participant_uri names it.
Otherwise anyone holding a token the verifier accepts could add a key to
another member and sign as that member. Mirrors participant_rejoin.test.ts.
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
    claims = {"good": {"sub": "alice", "auth_time": 111,
                       "chap_participant_uri": "human:alice"}}
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


# ------------------------------------------------ a token binds to its owner

def _key(kid):
    return {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": kid.upper()}


TOKENS = {
    "alice-1": {"sub": "alice", "auth_time": 111, "cnf": {"jwk": _key("alice-k1")}},
    "alice-2": {"sub": "alice", "auth_time": 222, "cnf": {"jwk": _key("alice-k2")}},
    "mallory": {"sub": "mallory", "auth_time": 333, "cnf": {"jwk": _key("mallory-k")}},
    "mallory-naming-alice": {"sub": "mallory", "auth_time": 333,
                             "chap_participant_uri": "human:alice",
                             "cnf": {"jwk": _key("mallory-k")}},
    "naming-mallory": {"sub": "x", "auth_time": 1, "chap_participant_uri": "human:mallory"},
    "alice-unnamed": {"sub": "alice", "auth_time": 444, "cnf": {"jwk": _key("alice-k9")}},
}


def _oidc_ready():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True,
                                       verify_oidc_token=lambda t: TOKENS.get(t)))

    def join(uri, **extra):
        return c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                           "params": {"workspace": "w", "from": uri, "type": "human", **extra}})

    c.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                "params": {"workspace": "w"}})
    return c, join


def _member(c, uri="human:alice"):
    return c.get_workspace("w").members[uri]


def test_rejoin_with_another_subjects_token_is_refused():
    c, join = _oidc_ready()
    assert "error" not in join("human:alice", role="admin", oidc_token="alice-1")
    r = join("human:alice", oidc_token="mallory")
    assert r["error"]["code"] == -32404
    m = _member(c)
    assert [k.kid for k in m.keys] == ["alice-k1"]
    assert m.oidc_sub == "alice"
    assert m.oidc_auth_time == 111


def test_rejoin_with_a_token_naming_the_member_but_another_subject_is_refused():
    c, join = _oidc_ready()
    join("human:alice", oidc_token="alice-1")
    r = join("human:alice", oidc_token="mallory-naming-alice")
    assert r["error"]["code"] == -32404
    assert [k.kid for k in _member(c).keys] == ["alice-k1"]


def test_rejoin_with_the_same_subject_adds_a_key_and_refreshes():
    c, join = _oidc_ready()
    join("human:alice", oidc_token="alice-1")
    assert "error" not in join("human:alice", oidc_token="alice-2")
    m = _member(c)
    assert [k.kid for k in m.keys] == ["alice-k1", "alice-k2"]
    assert m.oidc_auth_time == 222


def test_binding_an_unbound_member_needs_a_token_naming_it():
    c, join = _oidc_ready()
    join("human:alice", jwks={"keys": [_key("alice-self")]})
    r = join("human:alice", oidc_token="alice-unnamed")
    assert r["error"]["code"] == -32404
    m = _member(c)
    assert m.oidc_sub is None
    assert [k.kid for k in m.keys] == ["alice-self"]


def test_a_token_naming_another_participant_is_refused_on_first_join():
    c, join = _oidc_ready()
    r = join("human:alice", oidc_token="naming-mallory")
    assert r["error"]["code"] == -32404
    assert "human:alice" not in c.get_workspace("w").members


PRESENTATIONS = {
    "alice": {"holder": "did:example:alice", "cnf_jwk": _key("alice-vp")},
    "alice-again": {"holder": "did:example:alice", "cnf_jwk": _key("alice-vp2")},
    "mallory": {"holder": "did:example:mallory", "cnf_jwk": _key("mallory-vp")},
}


def _vc_ready():
    c = Coordinator(CoordinatorOptions(
        deterministic_ids=True,
        verify_vc=lambda vp: PRESENTATIONS.get(vp.get("who"))))

    def join(uri, who=None, **extra):
        params = {"workspace": "w", "from": uri, "type": "human", **extra}
        if who is not None:
            params["vc_presentation"] = {"who": who}
        return c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                           "params": params})

    c.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                "params": {"workspace": "w"}})
    return c, join


def test_rejoin_with_another_holders_presentation_is_refused():
    c, join = _vc_ready()
    join("human:alice", who="alice")
    r = join("human:alice", who="mallory")
    assert r["error"]["code"] == -32411
    m = _member(c)
    assert [k.kid for k in m.keys] == ["alice-vp"]
    assert m.vc_holder == "did:example:alice"


def test_rejoin_with_the_same_holder_adds_a_key():
    c, join = _vc_ready()
    join("human:alice", who="alice")
    assert "error" not in join("human:alice", who="alice-again")
    assert [k.kid for k in _member(c).keys] == ["alice-vp", "alice-vp2"]


def test_a_presentation_cannot_bind_an_unbound_member():
    c, join = _vc_ready()
    join("human:alice", jwks={"keys": [_key("alice-self")]})
    r = join("human:alice", who="alice")
    assert r["error"]["code"] == -32411
    m = _member(c)
    assert m.vc_holder is None
    assert [k.kid for k in m.keys] == ["alice-self"]


# ------------------------------------------------------------- edge cases

def test_the_four_refusals_carry_their_messages():
    c, join = _oidc_ready()
    assert join("human:alice", oidc_token="naming-mallory")["error"]["message"] == \
        "OIDC token is bound to another participant"
    join("human:alice", oidc_token="alice-1")
    assert join("human:alice", oidc_token="mallory")["error"]["message"] == \
        "OIDC token subject does not match the member"
    join("human:bob", jwks={"keys": [_key("bob-self")]})
    assert join("human:bob", oidc_token="alice-unnamed")["error"]["message"] == \
        "OIDC token does not name this member"
    v, vjoin = _vc_ready()
    vjoin("human:alice", who="alice")
    assert vjoin("human:alice", who="mallory")["error"]["message"] == \
        "Presentation holder does not match the member"


def test_a_good_token_with_another_holders_presentation_changes_nothing():
    tokens = {"alice": {"sub": "alice", "auth_time": 5, "cnf": {"jwk": _key("alice-k1")}},
              "alice-later": {"sub": "alice", "auth_time": 6, "cnf": {"jwk": _key("alice-k2")}}}
    c = Coordinator(CoordinatorOptions(
        deterministic_ids=True,
        verify_oidc_token=lambda t: tokens.get(t),
        verify_vc=lambda vp: PRESENTATIONS.get(vp.get("who"))))

    def join(**extra):
        return c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                           "params": {"workspace": "w", "from": "human:alice",
                                      "type": "human", **extra}})

    join(oidc_token="alice", vc_presentation={"who": "alice"})
    r = join(oidc_token="alice-later", vc_presentation={"who": "mallory"})
    assert r["error"]["code"] == -32411
    m = _member(c)
    assert [k.kid for k in m.keys] == ["alice-k1", "alice-vp"]
    assert m.oidc_auth_time == 5


def test_a_token_with_no_string_subject_cannot_rejoin_a_bound_member():
    tokens = {"alice": {"sub": "alice", "auth_time": 1},
              "no-sub": {"auth_time": 2}, "number-sub": {"sub": 7, "auth_time": 2}}
    c = Coordinator(CoordinatorOptions(deterministic_ids=True,
                                       verify_oidc_token=lambda t: tokens.get(t)))

    def join(token):
        return c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                           "params": {"workspace": "w", "from": "human:alice",
                                      "type": "human", "oidc_token": token}})

    join("alice")
    assert join("no-sub")["error"]["code"] == -32404
    assert join("number-sub")["error"]["code"] == -32404
    assert _member(c).oidc_sub == "alice"


def test_a_non_string_participant_uri_claim_is_refused():
    c = Coordinator(CoordinatorOptions(
        deterministic_ids=True,
        verify_oidc_token=lambda t: {"sub": "alice", "chap_participant_uri": 42}))
    r = c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "w", "from": "human:alice", "type": "human",
                               "oidc_token": "t"}})
    assert r["error"]["code"] == -32404


def test_an_empty_holder_falls_back_to_the_id_and_binds_nothing_else():
    presentations = {"first": {"holder": "", "id": "did:example:alice"},
                     "other": {"holder": "", "id": "did:example:mallory"}}
    c = Coordinator(CoordinatorOptions(
        deterministic_ids=True,
        verify_vc=lambda vp: presentations.get(vp.get("who"))))

    def join(who):
        return c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                           "params": {"workspace": "w", "from": "human:alice",
                                      "type": "human", "vc_presentation": {"who": who}}})

    join("first")
    assert _member(c).vc_holder == "did:example:alice"
    assert join("other")["error"]["code"] == -32411


def test_a_rejoin_token_without_acr_clears_the_old_acr():
    import time
    fresh = int(time.time())
    tokens = {"strong": {"sub": "alice", "auth_time": fresh, "acr": "mfa"},
              "plain": {"sub": "alice", "auth_time": fresh}}
    c = Coordinator(CoordinatorOptions(enforce_step_up=True,
                                       verify_oidc_token=lambda t: tokens.get(t)))
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w", "min_acr": "mfa"}})

    def join(token):
        return c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                           "params": {"workspace": "w", "from": "human:alice", "type": "human",
                                      "role": "admin", "oidc_token": token}})

    def privileged():
        return c.dispatch({"jsonrpc": "2.0", "id": "p", "method": "workspace.set_profiles",
                           "params": {"workspace": "w", "from": "human:alice",
                                      "profiles": ["core/1.0", "review/1.0"]}})

    join("strong")
    assert privileged().get("error", {}).get("code") != -32402
    assert "error" not in join("plain")
    assert c.get_workspace("w").members["human:alice"].oidc_acr is None
    assert privileged()["error"]["code"] == -32402
